import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from export_to_gcs import (
    DEFAULT_COLLECTION,
    DEFAULT_FORMAT,
    export_documents,
    get_mongo_client,
    build_query_sets,
)
from secure_config import get_config

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("monitor")


def print_status(stage: str, ok: bool, message: str = "") -> None:
    status = "Ok" if ok else "Failed"
    line = f"{stage}: {status}"
    if message:
        line += f" - {message}"
    print(line)


def check_config() -> Tuple[bool, str]:
    config = get_config()
    if config.validate():
        return True, "Configuration is valid"
    return False, "Configuration validation failed"


def check_mongo_connection() -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    try:
        client = get_mongo_client()
        db_config = get_config().get_db_config()
        db = client[db_config["db_name"]]
        return True, "MongoDB connection succeeded", {"client": client, "db": db}
    except Exception as exc:
        return False, str(exc), None


def check_query_sets(db, collection_name: str) -> Tuple[bool, str, Dict[str, int], Dict[str, List[Dict[str, Any]]]]:
    query_sets = build_query_sets()
    counts: Dict[str, int] = {}
    samples: Dict[str, List[Dict[str, Any]]] = {}

    try:
        collection = db[collection_name]
        for name, options in query_sets.items():
            count = collection.count_documents(options["query"])
            counts[name] = count
            if count > 0:
                cursor = collection.find(options["query"], options["projection"]).limit(5)
                samples[name] = [doc for doc in cursor]
            else:
                samples[name] = []
        return True, "Query sets executed successfully", counts, samples
    except Exception as exc:
        return False, str(exc), {}, {}


def check_export_sample_files(db, collection_name: str, output_format: str) -> Tuple[bool, str]:
    query_sets = build_query_sets()

    with tempfile.TemporaryDirectory(prefix="pipeline_monitor_") as temp_dir:
        output_dir = Path(temp_dir)
        try:
            collection = db[collection_name]
            for name, options in query_sets.items():
                output_path = export_documents(
                    db_collection=collection,
                    name=name,
                    query=options["query"],
                    projection=options["projection"],
                    output_dir=output_dir,
                    output_format=output_format,
                    batch_size=10,
                    max_documents=1,
                )
                if not output_path.exists() or output_path.stat().st_size == 0:
                    raise RuntimeError(f"Exported file is empty or missing: {output_path}")
            return True, f"Exported sample files successfully in temporary directory {output_dir}"
        except Exception as exc:
            return False, str(exc)


def check_gcs_upload() -> Tuple[bool, str]:
    bucket_name = os.getenv("GCS_BUCKET")
    if not bucket_name:
        return False, "GCS_BUCKET is not configured"

    try:
        from google.cloud import storage
    except Exception as exc:
        return False, f"google-cloud-storage is not installed: {exc}"

    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as tmp_file:
            tmp_file.write("pipeline monitor health check\n")
            tmp_path = Path(tmp_file.name)

        destination_blob = f"pipeline_monitor/{tmp_path.name}"
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(destination_blob)
        blob.upload_from_filename(str(tmp_path))
        blob.delete()
        tmp_path.unlink(missing_ok=True)
        return True, f"GCS upload to gs://{bucket_name}/{destination_blob} succeeded"
    except Exception as exc:
        return False, str(exc)


def check_bigquery_dataset() -> Tuple[bool, str]:
    project = os.getenv("BQ_PROJECT")
    dataset = os.getenv("BQ_RAW_DATASET")
    if not dataset:
        return False, "BQ_RAW_DATASET is not configured"

    try:
        from google.cloud import bigquery
        from google.api_core.exceptions import NotFound
    except Exception as exc:
        return False, f"google-cloud-bigquery is not installed or cannot be imported: {exc}"

    try:
        client = bigquery.Client(project=project) if project else bigquery.Client()
        dataset_ref = dataset if "." in dataset else f"{client.project}.{dataset}"
        client.get_dataset(dataset_ref)
        return True, f"BigQuery dataset exists: {dataset_ref}"
    except NotFound:
        return False, f"BigQuery dataset does not exist: {dataset_ref}"
    except Exception as exc:
        return False, str(exc)


def evaluate_data_quality(counts: Dict[str, int], samples: Dict[str, List[Dict[str, Any]]]) -> Tuple[str, Dict[str, Any]]:
    quality_summary: Dict[str, Any] = {}
    total_score = 0
    total_weight = 0

    rules = {
        "raw_data": {"required": ["_id", "collection", "timestamp"]},
        "ip2location": {"required": ["ip", "timestamp"]},
        "product_data": {"required": [["product_id", "viewing_product_id"], "collection"]},
    }

    for name, docs in samples.items():
        count = counts.get(name, 0)
        if count == 0:
            quality_summary[name] = {
                "status": "No data",
                "count": 0,
                "coverage": 0.0,
            }
            continue

        required = rules.get(name, {}).get("required", [])
        coverage_scores: List[float] = []
        for doc in docs:
            doc_total = 0
            doc_present = 0
            for rule in required:
                if isinstance(rule, list):
                    doc_total += 1
                    if any(doc.get(field) not in (None, "") for field in rule):
                        doc_present += 1
                else:
                    doc_total += 1
                    if doc.get(rule) not in (None, ""):
                        doc_present += 1
            coverage_scores.append(doc_present / max(doc_total, 1))

        coverage = sum(coverage_scores) / max(len(coverage_scores), 1)
        if coverage >= 0.9:
            status = "Good"
            score = 2
        elif coverage >= 0.6:
            status = "Moderate"
            score = 1
        else:
            status = "Poor"
            score = 0

        quality_summary[name] = {
            "status": status,
            "count": count,
            "coverage": round(coverage, 3),
            "sample_size": len(docs),
        }
        total_score += score
        total_weight += 1

    if total_weight == 0:
        overall = "No data"
    elif total_score / total_weight >= 1.5:
        overall = "Good"
    elif total_score / total_weight >= 0.8:
        overall = "Moderate"
    else:
        overall = "Poor"

    return overall, quality_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end pipeline health check and data quality monitor")
    parser.add_argument("--gcs", action="store_true", help="Check GCS upload connectivity")
    parser.add_argument("--bq", action="store_true", help="Check BigQuery dataset connectivity")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="MongoDB collection name to query")
    parser.add_argument("--format", default=DEFAULT_FORMAT, choices=["csv", "jsonl", "parquet"], help="Local export format for sample export check")
    args = parser.parse_args()

    overall_ok = True

    ok, message = check_config()
    print_status("Config validation", ok, message)
    overall_ok &= ok

    ok, message, mongo_info = check_mongo_connection()
    print_status("MongoDB connectivity", ok, message)
    overall_ok &= ok

    counts: Dict[str, int] = {}
    samples: Dict[str, List[Dict[str, Any]]] = {}
    if ok and mongo_info:
        db = mongo_info["db"]
        ok, message, counts, samples = check_query_sets(db, args.collection)
        print_status("MongoDB query set check", ok, message)
        overall_ok &= ok

        if ok:
            ok, message = check_export_sample_files(db, args.collection, args.format)
            print_status("Sample export check", ok, message)
            overall_ok &= ok

    if args.gcs:
        ok, message = check_gcs_upload()
        print_status("GCS upload check", ok, message)
        overall_ok &= ok
    else:
        print("GCS upload check: Skipped")

    if args.bq:
        ok, message = check_bigquery_dataset()
        print_status("BigQuery dataset check", ok, message)
        overall_ok &= ok
    else:
        print("BigQuery dataset check: Skipped")

    if counts:
        print("\nData quality summary:")
        overall_quality, quality_summary = evaluate_data_quality(counts, samples)
        for name, summary in quality_summary.items():
            print(f"  {name}: status={summary['status']}, count={summary['count']}, coverage={summary['coverage']}")
        print(f"Overall data quality: {overall_quality}")

    print(f"\nPipeline health status: {'Ok' if overall_ok else 'Failed'}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
