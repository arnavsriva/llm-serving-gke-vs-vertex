#!/usr/bin/env bash
# Create the GCS bucket that holds Terraform state (once per project): versioned, uniform access,
# public access prevented. Billable but negligible (a few KB). Reads GCP_PROJECT_ID,
# TF_STATE_BUCKET and GCP_REGION from the environment (make passes .env through).
set -euo pipefail

: "${GCP_PROJECT_ID:?set GCP_PROJECT_ID (see .env.example)}"
: "${TF_STATE_BUCKET:?set TF_STATE_BUCKET (see .env.example)}"
REGION="${GCP_REGION:-us-central1}"

if gcloud storage buckets describe "gs://${TF_STATE_BUCKET}" --project "$GCP_PROJECT_ID" >/dev/null 2>&1; then
  echo "gs://${TF_STATE_BUCKET} already exists"
  exit 0
fi

gcloud storage buckets create "gs://${TF_STATE_BUCKET}" \
  --project "$GCP_PROJECT_ID" \
  --location "$REGION" \
  --uniform-bucket-level-access \
  --public-access-prevention
gcloud storage buckets update "gs://${TF_STATE_BUCKET}" --project "$GCP_PROJECT_ID" --versioning
echo "created gs://${TF_STATE_BUCKET} (versioned) in $REGION"
