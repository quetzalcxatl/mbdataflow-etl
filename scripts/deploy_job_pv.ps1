# ============================================================
# MBDataFlow_ETL — Create Cloud Run Job for pipeline_PV
# Run once to create the Job. For updates, use deploy_pv.ps1
# ============================================================

$PROJECT_ID  = gcloud config get-value project
$REGION      = "us-central1"
$SA_EMAIL    = "mbdataflow-runner@$PROJECT_ID.iam.gserviceaccount.com"
$IMAGE       = "$REGION-docker.pkg.dev/$PROJECT_ID/mbdataflow/etl-pipelines:latest"

# Notes on config values:
#   * memory=1Gi (two small CSVs, ~1.7k rows per shift; Chrome is the main consumer)
#   * task-timeout=15m (end-to-end expected ~2-3 min: login + 2 direct downloads + load)
#   * max-retries=1 is SAFE: BigQueryDayLoader is delete-then-append per day, so a
#     retry after a partial run replaces the day instead of duplicating it
#   * BQ_PROJECT must be set (no default in settings.py); BQ_DATASET_SONDA uses the
#     default "Sonda" from settings.py — matching production
#   * --target,prod is REQUIRED: pipeline_PV defaults to the test table
#     (pruebas.PV_smoketest) so local runs never touch production. Without this
#     flag the Job would load to the test table and Sonda.PV would silently stop
#     receiving data.

gcloud run jobs create pipeline-pv `
  --image=$IMAGE `
  --command=python `
  --args="-m,pipelines.pipeline_PV,--target,prod" `
  --service-account=$SA_EMAIL `
  --region=$REGION `
  --max-retries=1 `
  --task-timeout=15m `
  --memory=1Gi `
  --cpu=1 `
  --set-env-vars="GCP_PROJECT_ID=$PROJECT_ID" `
  --set-env-vars="BQ_PROJECT=$PROJECT_ID" `
  --set-secrets="SONDA_QUERY_USER=SONDA_QUERY_USER:latest" `
  --set-secrets="SONDA_QUERY_PASSWORD=SONDA_QUERY_PASSWORD:latest"
