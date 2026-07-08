#!/usr/bin/env bash
# Deployment helper for the Cloud Function that loads GCS objects into BigQuery
# Edit the variables below as needed before running.

set -euo pipefail

# Required settings
PROJECT=${PROJECT:-$(gcloud config get-value project 2>/dev/null)}
REGION=${REGION:-us-central1}
FUNCTION_NAME=${FUNCTION_NAME:-trigger-bigquery-load}
BUCKET_TRIGGER=${BUCKET_TRIGGER:-YOUR_BUCKET_NAME}

# Optional environment variables for the function
BQ_RAW_DATASET=${BQ_RAW_DATASET:-raw}
BQ_TABLE_PREFIX=${BQ_TABLE_PREFIX:-gcs_}

echo "Deploying Cloud Function '${FUNCTION_NAME}' to project=${PROJECT} region=${REGION}"

if [ -z "${PROJECT}" ]; then
  echo "No GCloud project set. Set PROJECT env or run 'gcloud config set project PROJECT_ID'" >&2
  exit 1
fi

echo "Using dataset=${BQ_RAW_DATASET}, table_prefix=${BQ_TABLE_PREFIX}, bucket trigger=${BUCKET_TRIGGER}"

# Upload source (deploy from repository root)
# Create a requirements file in the repo root named 'cloud_function_requirements.txt' that lists google-cloud-bigquery

gcloud functions deploy ${FUNCTION_NAME} \
  --runtime python311 \
  --trigger-resource ${BUCKET_TRIGGER} \
  --trigger-event google.storage.object.finalize \
  --region ${REGION} \
  --project ${PROJECT} \
  --entry-point trigger_bigquery_load \
  --set-env-vars BQ_RAW_DATASET=${BQ_RAW_DATASET},BQ_TABLE_PREFIX=${BQ_TABLE_PREFIX} \
  --source . \
  --quiet

echo "Deployment requested. Use 'gcloud functions logs read ${FUNCTION_NAME} --region ${REGION} --project ${PROJECT} --limit 50' to view logs." 
