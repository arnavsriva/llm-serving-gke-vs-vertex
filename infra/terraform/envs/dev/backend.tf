# Remote state in GCS. Bucket is supplied at init time so no names are hardcoded:
#   terraform init -backend-config="bucket=$TF_STATE_BUCKET"
terraform {
  backend "gcs" {
    prefix = "llm-serving/dev"
  }
}
