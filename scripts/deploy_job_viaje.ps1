# ============================================================
# MBDataFlow_ETL — Create Cloud Run Job for pipeline_Viaje
# Run once to create the Job. For updates, use deploy_viaje.ps1
# ============================================================

$PROJECT_ID  = gcloud config get-value project
$REGION      = "us-central1"
$SA_EMAIL    = "mbdataflow-runner@$PROJECT_ID.iam.gserviceaccount.com"
$IMAGE       = "$REGION-docker.pkg.dev/$PROJECT_ID/mbdataflow/etl-pipelines:latest"

# IMPORTANT: replace the DRIVE_RV_FOLDER_ID value below with your real value
# from .env (same folder used in PR2 testing) before running this script for
# the first time.
#
# Notes on config values:
#   * memory=2Gi (transform holds raw CSV + 2 processed DataFrames + IsolationForest
#     per-route in memory; 1Gi is tight, 2Gi is comfortable — verified empirically)
#   * task-timeout=30m (end-to-end runs ~3-5 min in production; 30m is 6-10x buffer,
#     safely under any billable ceiling for a weekly job)
#   * BQ_PROJECT must be set (no default in settings.py); BQ_DATASET_SONDA and
#     BQ_DATASET_INTERTRAMOS use defaults "Sonda" and "TIEMPO_INTERTRAMOS" from
#     settings.py — matching production, no need to set explicitly
#   * DRIVE_BACKUP=true set explicitly for production clarity even though it's
#     the default in code (PR2 decision)

gcloud run jobs create pipeline-viaje `
  --image=$IMAGE `
  --command=python `
  --args="-m,pipelines.pipeline_Viaje" `
  --service-account=$SA_EMAIL `
  --region=$REGION `
  --max-retries=1 `
  --task-timeout=35m `
  --memory=2Gi `
  --cpu=1 `
  --set-env-vars="GCP_PROJECT_ID=$PROJECT_ID" `
  --set-env-vars="BQ_PROJECT=$PROJECT_ID" `
  --set-env-vars="DRIVE_BACKUP=true" `
  --set-env-vars="DRIVE_RV_FOLDER_ID=11F11XjPw3IXNG-Uv-wUDdDx3ftif4RZD" `
  --set-secrets="SONDA_QUERY_USER=SONDA_QUERY_USER:latest" `
  --set-secrets="SONDA_QUERY_PASSWORD=SONDA_QUERY_PASSWORD:latest"