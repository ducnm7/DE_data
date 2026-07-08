import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

try:
    from pymongo import MongoClient
except ImportError:  # pragma: no cover
    MongoClient = None

# Ensure scripts package is importable when running from repo root
ROOT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from secure_config import get_config

try:
    from bson import json_util
except ImportError:  # pragma: no cover
    json_util = None

JSON_DEFAULT_KWARGS = {"default": json_util.default if json_util else str, "ensure_ascii": False}

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = os.getenv("EXPORT_OUTPUT_DIR", "exports")
DEFAULT_BATCH_SIZE = int(os.getenv("EXPORT_BATCH_SIZE", "10000"))
DEFAULT_FORMAT = os.getenv("EXPORT_FORMAT", "jsonl").lower()
DEFAULT_COLLECTION = os.getenv("EXPORT_COLLECTION", "summary")
DEFAULT_GCS_PREFIX = os.getenv("GCS_DEST_PREFIX", "exports/")

config = get_config()


def configure_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> None:
    if logger.handlers:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(console_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(file_handler)

    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_mongo_client() -> MongoClient:
    try:
        mongo_uri = config.get_mongo_uri()
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        client.server_info()
        logger.info("Connected to MongoDB")
        return client
    except Exception as exc:
        logger.exception("Failed to connect to MongoDB")
        raise RuntimeError("MongoDB connection failed") from exc


def stream_documents(collection, query: Dict[str, Any], projection: Optional[Dict[str, int]], batch_size: int) -> Iterator[Dict[str, Any]]:
    cursor = collection.find(query, projection, batch_size=batch_size, no_cursor_timeout=True)
    try:
        for document in cursor:
            yield document
    finally:
        cursor.close()


def normalize_document(document: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}

    for key, value in document.items():
        if key == "_id":
            normalized[key] = str(value)
            continue

        if isinstance(value, (dict, list)):
            try:
                normalized[key] = json.dumps(value, default=json_util.default, ensure_ascii=False)
            except Exception:
                normalized[key] = str(value)
            continue

        if isinstance(value, bytes):
            try:
                normalized[key] = value.decode("utf-8", errors="replace")
            except Exception:
                normalized[key] = str(value)
            continue

        normalized[key] = value

    return normalized


def export_to_csv(output_path: Path, documents: Iterator[Dict[str, Any]]) -> int:
    row_count = 0
    writer: Optional[csv.DictWriter] = None

    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        for document in documents:
            normalized = normalize_document(document)

            if writer is None:
                fieldnames = sorted(normalized.keys())
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()

            writer.writerow(normalized)
            row_count += 1

    logger.info("Wrote %d rows to %s", row_count, output_path)
    return row_count


def export_to_jsonl(output_path: Path, documents: Iterator[Dict[str, Any]]) -> int:
    row_count = 0

    with output_path.open("w", encoding="utf-8") as output_file:
        for document in documents:
            output_file.write(json.dumps(document, **JSON_DEFAULT_KWARGS))
            output_file.write("\n")
            row_count += 1

    logger.info("Wrote %d rows to %s", row_count, output_path)
    return row_count


def export_to_parquet(output_path: Path, documents: Iterator[Dict[str, Any]], batch_size: int) -> int:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        logger.error("Parquet export requires pyarrow. Install it via 'pip install pyarrow'.")
        raise RuntimeError("pyarrow is not installed") from exc

    writer: Optional[pq.ParquetWriter] = None
    row_count = 0
    batch: List[Dict[str, Any]] = []

    try:
        for document in documents:
            batch.append(normalize_document(document))

            if len(batch) >= batch_size:
                table = pa.Table.from_pylist(batch)
                if writer is None:
                    writer = pq.ParquetWriter(output_path, table.schema, compression="snappy")
                writer.write_table(table)
                row_count += len(batch)
                batch = []

        if batch:
            table = pa.Table.from_pylist(batch)
            if writer is None:
                writer = pq.ParquetWriter(output_path, table.schema, compression="snappy")
            writer.write_table(table)
            row_count += len(batch)

    finally:
        if writer:
            writer.close()

    logger.info("Wrote %d rows to %s", row_count, output_path)
    return row_count


def upload_to_gcs(local_path: Path, bucket_name: str, destination_blob: str) -> None:
    try:
        from google.cloud import storage
    except ImportError as exc:
        logger.error("GCS upload requires google-cloud-storage. Install it via 'pip install google-cloud-storage'.")
        raise RuntimeError("google-cloud-storage is not installed") from exc

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(destination_blob)

    logger.info("Uploading %s to gs://%s/%s", local_path, bucket_name, destination_blob)
    blob.upload_from_filename(str(local_path))
    logger.info("Upload complete: gs://%s/%s", bucket_name, destination_blob)


def export_documents(
    db_collection,
    name: str,
    query: Dict[str, Any],
    projection: Optional[Dict[str, int]],
    output_dir: Path,
    output_format: str,
    batch_size: int,
    max_documents: Optional[int] = None,
) -> Path:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{name}_{timestamp}.{output_format}"

    if output_format == "csv":
        exporter = export_to_csv
    elif output_format == "jsonl":
        exporter = export_to_jsonl
    elif output_format == "parquet":
        exporter = lambda path, docs: export_to_parquet(path, docs, batch_size)
    else:
        raise ValueError(f"Unsupported format: {output_format}")

    cursor = stream_documents(db_collection, query, projection, batch_size)
    logger.info("Exporting '%s' with query=%s to %s", name, query, output_path)
    row_count = 0

    if max_documents is not None and max_documents > 0:
        limited_cursor = (doc for i, doc in enumerate(cursor) if i < max_documents)
        row_count = exporter(output_path, limited_cursor)
    else:
        row_count = exporter(output_path, cursor)

    logger.info("Export finished for '%s': %d rows", name, row_count)
    return output_path


def export_sample_data(output_dir: Path, output_format: str) -> List[Path]:
    sample_sets = {
        "raw_data_sample": [
            {"_id": "raw1", "collection": "view_product_detail", "product_id": "P100", "ip": "8.8.8.8", "timestamp": 1688400000},
            {"_id": "raw2", "collection": "add_to_cart_action", "product_id": "P101", "ip": "1.1.1.1", "timestamp": 1688400300},
        ],
        "ip2location_sample": [
            {"ip": "8.8.8.8", "country": "US", "city": "Mountain View", "latitude": 37.386, "longitude": -122.0838},
            {"ip": "1.1.1.1", "country": "AU", "city": "Sydney", "latitude": -33.8688, "longitude": 151.2093},
        ],
        "product_sample": [
            {"product_id": "P100", "url": "https://example.com/product/P100", "name": "Sample Ring", "price": 199.99},
            {"product_id": "P101", "url": "https://example.com/product/P101", "name": "Sample Necklace", "price": 249.99},
        ],
    }

    output_paths: List[Path] = []
    for name, documents in sample_sets.items():
        output_path = output_dir / f"{name}.{output_format}"
        logger.info("Creating sample export %s", output_path)

        if output_format == "csv":
            export_to_csv(output_path, iter(documents))
        elif output_format == "jsonl":
            export_to_jsonl(output_path, iter(documents))
        elif output_format == "parquet":
            export_to_parquet(output_path, iter(documents), batch_size=1000)
        else:
            raise ValueError(f"Unsupported sample format: {output_format}")

        output_paths.append(output_path)

    return output_paths


def build_query_sets() -> Dict[str, Dict[str, Any]]:
    return {
        "raw_data": {
            "query": {},
            "projection": None,
        },
        "ip2location": {
            "query": {
                "$and": [
                    {"ip": {"$exists": True, "$ne": None, "$ne": ""}},
                    {"ip": {"$not": {"$regex": r"^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)"}}},
                ]
            },
            "projection": {"_id": 0, "ip": 1, "collection": 1, "timestamp": 1},
        },
        "product_data": {
            "query": {
                "$or": [
                    {"product_id": {"$exists": True, "$ne": None, "$ne": ""}},
                    {"viewing_product_id": {"$exists": True, "$ne": None, "$ne": ""}},
                ]
            },
            "projection": {"_id": 0, "product_id": 1, "viewing_product_id": 1, "collection": 1, "current_url": 1, "referrer_url": 1, "timestamp": 1},
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export MongoDB data and optionally upload to Google Cloud Storage.")
    parser.add_argument("--format", choices=["csv", "jsonl", "parquet"], default=DEFAULT_FORMAT, help="Export file format")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Local directory for exports")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Batch size when streaming from MongoDB")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="MongoDB collection to export from")
    parser.add_argument("--gcs-bucket", default=os.getenv("GCS_BUCKET"), help="Google Cloud Storage bucket name")
    parser.add_argument("--gcs-prefix", default=DEFAULT_GCS_PREFIX, help="GCS destination prefix")
    parser.add_argument("--upload", action="store_true", help="Upload exported files to GCS")
    parser.add_argument("--sample", action="store_true", help="Create sample export files instead of querying MongoDB")
    parser.add_argument("--max-docs", type=int, default=None, help="Maximum number of documents to export for each data set")
    parser.add_argument("--log-file", default=None, help="Optional log file path")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Logging verbosity")
    return parser.parse_args()


def make_output_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    args = parse_args()
    configure_logging(args.log_level, args.log_file)

    if not config.validate():
        raise RuntimeError("Configuration validation failed. Check your environment variables or .env file.")

    output_dir = make_output_dir(Path(args.output_dir))

    if args.sample:
        sample_files = export_sample_data(output_dir, args.format)
        logger.info("Created %d sample export files in %s", len(sample_files), output_dir)
        return

    client = None
    try:
        client = get_mongo_client()
        db_config = config.get_db_config()
        db = client[db_config["db_name"]]
        collection = db[args.collection]

        query_sets = build_query_sets()
        exported_files: List[Path] = []

        for name, options in query_sets.items():
            output_path = export_documents(
                db_collection=collection,
                name=name,
                query=options["query"],
                projection=options["projection"],
                output_dir=output_dir,
                output_format=args.format,
                batch_size=args.batch_size,
                max_documents=args.max_docs,
            )
            exported_files.append(output_path)

        if args.upload:
            if not args.gcs_bucket:
                logger.error("GCS bucket name is required for upload")
                raise RuntimeError("Missing GCS bucket for upload")

            for path in exported_files:
                destination_blob = f"{args.gcs_prefix.rstrip('/')}/{path.name}" if args.gcs_prefix else path.name
                upload_to_gcs(path, args.gcs_bucket, destination_blob)

    except Exception as exc:
        logger.exception("Export process failed")
        raise
    finally:
        if client is not None:
            client.close()
            logger.info("MongoDB connection closed")


if __name__ == "__main__":
    main()
