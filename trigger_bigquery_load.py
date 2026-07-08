"""
Cloud Function: trigger_bigquery_load

Detects new files in GCS and starts a BigQuery load job into the raw layer.

Environment variables (recommended):
- BQ_PROJECT: GCP project id (defaults to client project)
- BQ_RAW_DATASET: BigQuery dataset for raw layer (default: raw)
- BQ_TABLE_PREFIX: Optional prefix for destination tables (default: gcs_)

The function is safe to import locally (it defers Google imports until runtime).
"""
import os
import logging
import json
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("trigger_bigquery_load")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _detect_source_format(object_name: str) -> str:
    ext = object_name.rsplit(".", 1)[-1].lower() if "." in object_name else ""
    if ext == "jsonl":
        return "NEWLINE_DELIMITED_JSON"
    if ext == "csv":
        return "CSV"
    if ext == "parquet":
        return "PARQUET"
    raise ValueError(f"Unsupported source format for object: {object_name}")


def _is_supported_source(object_name: str) -> bool:
    ext = object_name.rsplit(".", 1)[-1].lower() if "." in object_name else ""
    return ext in ("jsonl", "csv", "parquet")


def _build_table_name(object_name: str, prefix: Optional[str]) -> str:
    # Use the top-level folder or file stem for stable table naming,
    # instead of the full path, so objects of the same data type map to the same table.
    path = Path(object_name)
    ext = path.suffix.lower()

    if ext in {".jsonl", ".csv", ".parquet"}:
        if len(path.parts) > 1:
            name = path.parts[0]
        else:
            name = path.stem
    else:
        name = object_name

    name = name.replace("/", "__").replace("\\", "__").replace(".", "_")

    if prefix:
        return f"{prefix}{name}"
    return name


def _ensure_dataset_exists(client, dataset: str) -> None:
    try:
        from google.api_core.exceptions import NotFound
    except Exception:
        NotFound = Exception

    dataset_ref = dataset if "." in dataset else f"{client.project}.{dataset}"
    try:
        client.get_dataset(dataset_ref)
    except NotFound:
        raise RuntimeError(f"BigQuery dataset does not exist: {dataset_ref}")


def load_gcs_to_bq(bucket: str, name: str, project: Optional[str] = None) -> Dict:
    """Start a BigQuery load job from gs://{bucket}/{name} into the raw dataset/table.

    Returns job metadata dict on success or raises an exception on failure.
    """
    # Defer GCP imports to runtime so module is importable locally without deps
    try:
        from google.cloud import bigquery
    except Exception as exc:  # pragma: no cover - environment-specific
        logger.exception("google-cloud-bigquery is required to run load jobs")
        raise

    project_env = project or os.getenv("BQ_PROJECT") or None
    dataset = os.getenv("BQ_RAW_DATASET", "glamira_raw")
    table_prefix = os.getenv("BQ_TABLE_PREFIX", "gcs_")

    client = bigquery.Client(project=project_env)

    if not _is_supported_source(name):
        raise ValueError(f"Unsupported object format: {name}")

    source_uri = f"gs://{bucket}/{name}"
    src_format = _detect_source_format(name)

    _ensure_dataset_exists(client, dataset)

    destination_table = _build_table_name(name, table_prefix)
    table_id = f"{client.project}.{dataset}.{destination_table}"

    job_config = bigquery.LoadJobConfig()
    # sensible defaults for raw layer
    job_config.autodetect = True
    job_config.write_disposition = bigquery.WriteDisposition.WRITE_APPEND
    job_config.create_disposition = bigquery.CreateDisposition.CREATE_IF_NEEDED

    if src_format == "CSV":
        job_config.source_format = bigquery.SourceFormat.CSV
        job_config.skip_leading_rows = 1
    elif src_format == "PARQUET":
        job_config.source_format = bigquery.SourceFormat.PARQUET
    elif src_format == "NEWLINE_DELIMITED_JSON":
        job_config.source_format = bigquery.SourceFormat.NEWLINE_DELIMITED_JSON

    logger.info("Starting BigQuery load job: %s -> %s (format=%s)", source_uri, table_id, src_format)
    start_time = time.monotonic()

    try:
        job = client.load_table_from_uri(source_uri, table_id, job_config=job_config)
        result = job.result()  # wait for completion
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        size_info = None
        try:
            from google.cloud import storage

            storage_client = storage.Client(project=project_env)
            bucket_obj = storage_client.bucket(bucket)
            blob = bucket_obj.get_blob(name)
            if blob:
                size_info = blob.size
        except Exception:
            size_info = None

        logger.info(
            "BigQuery job finished: %s state=%s, output_rows=%s, elapsed_ms=%s, bytes=%s, destination_table=%s",
            job.job_id,
            job.state,
            getattr(result, "output_rows", None),
            elapsed_ms,
            size_info,
            destination_table,
        )
        return {
            "job_id": job.job_id,
            "state": job.state,
            "output_rows": getattr(result, "output_rows", None),
            "elapsed_ms": elapsed_ms,
            "bytes": size_info,
        }
    except Exception as exc:
        logger.exception("BigQuery load job failed for %s", source_uri)
        raise


def trigger_bigquery_load(event: Dict, context) -> None:
    """Cloud Function entry point.

    Example `event` (Background GCS notification):
    {
      "bucket": "my-bucket",
      "name": "path/to/file.jsonl",
      "metageneration": "1",
      "timeCreated": "2020-09-29T11:32:00.000Z",
      "updated": "2020-09-29T11:32:00.000Z"
    }
    """
    logger.info("GCS event received: %s", json.dumps(event))

    bucket = event.get("bucket") or event.get("bucketId")
    name = event.get("name") or event.get("objectId")
    metageneration = event.get("metageneration")

    if not bucket or not name:
        logger.error("Invalid event payload: missing bucket or name")
        return

    if metageneration != "1":
        logger.info("Skipping non-initial object generation: gs://%s/%s metageneration=%s", bucket, name, metageneration)
        return

    if not _is_supported_source(name):
        logger.info("Skipping unsupported object type: gs://%s/%s", bucket, name)
        return

    try:
        result = load_gcs_to_bq(bucket, name)
        logger.info("Load result: %s", json.dumps(result))
    except Exception as exc:
        logger.exception("Failed to load object %s/%s into BigQuery", bucket, name)

