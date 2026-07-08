"""Helper to load specified GCS objects into BigQuery using trigger_bigquery_load.load_gcs_to_bq

Usage (local):
  python run_loads.py --bucket my-bucket ip2location_20260703_001525.jsonl product_data_20260703_002715.jsonl raw_data_20260702_233146.jsonl

The script defers Google BigQuery import until runtime; ensure `google-cloud-bigquery` is installed
and gcloud authentication is configured when running for real.
"""
import argparse
import logging
import sys
from typing import List

# Import helper functions for dry-run table naming and format detection
from trigger_bigquery_load import load_gcs_to_bq, _build_table_name, _detect_source_format

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_loads")


def main(bucket: str, objects: List[str], project: str = None, dry_run: bool = False):
    results = []
    dataset = project or None
    dataset = None  # not used for actual loads here
    table_prefix = None

    # read env defaults for preview
    dataset_name = None
    try:
        dataset_name = __import__('os').environ.get('BQ_RAW_DATASET', 'raw')
        table_prefix = __import__('os').environ.get('BQ_TABLE_PREFIX', 'gcs_')
    except Exception:
        dataset_name = 'raw'
        table_prefix = 'gcs_'

    for obj in objects:
        if dry_run:
            src_format = _detect_source_format(obj)
            dest_table = _build_table_name(obj, table_prefix)
            inferred_table_id = f"{project or '(project)'}.{dataset_name}.{dest_table}"
            logger.info("DRY RUN: gs://%s/%s -> %s (format=%s)", bucket, obj, inferred_table_id, src_format)
            results.append((obj, 'dry-run', {'table': inferred_table_id, 'format': src_format}))
            continue

        logger.info("Loading gs://%s/%s", bucket, obj)
        try:
            res = load_gcs_to_bq(bucket, obj, project)
            logger.info("Success: %s", res)
            results.append((obj, "ok", res))
        except Exception as e:
            logger.exception("Failed to load %s", obj)
            results.append((obj, "error", str(e)))

    # summary
    logger.info("Finished. Summary:")
    for obj, status, info in results:
        logger.info("%s -> %s: %s", obj, status, info)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load listed GCS objects into BigQuery raw layer")
    parser.add_argument("--bucket", required=True, help="GCS bucket name")
    parser.add_argument("--project", required=False, help="GCP project override")
    parser.add_argument("--dry-run", action="store_true", help="Preview inferred table IDs and formats without loading")
    parser.add_argument("objects", nargs="+", help="GCS object names to load")
    args = parser.parse_args()

    if not args.objects:
        print("No objects specified")
        sys.exit(1)

    main(args.bucket, args.objects, args.project, dry_run=args.dry_run)
