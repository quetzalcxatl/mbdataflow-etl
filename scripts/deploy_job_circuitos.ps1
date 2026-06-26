# ============================================================
# MBDataFlow_ETL — Create Cloud Run Job for pipeline_Circuitos
# Run once to create the Job. For updates, use deploy_circuitos.ps1
# ============================================================

$PROJECT_ID  = gcloud config get-value project
$REGION      = "us-central1"
$SA_EMAIL    = "mbdataflow-runner@$PROJECT_ID.iam.gserviceaccount.com"
$IMAGE       = "$REGION-docker.pkg.dev/$PROJECT_ID/mbdataflow/etl-pipelines:latest"

# IMPORTANT: replace the two folder IDs below with your real values
# from .env (DRIVE_CIRC_DESGLOSADO_FOLDER_ID, DRIVE_CIRC_EJECUTIVO_FOLDER_ID)
# before running this script for the first time.

gcloud run jobs create pipeline-circuitos `
  --image=$IMAGE `
  --command=python `
  --args="-m,pipelines.pipeline_Circuitos" `
  --service-account=$SA_EMAIL `
  --region=$REGION `
  --max-retries=1 `
  --task-timeout=30m `
  --memory=1Gi `
  --cpu=1 `
  --set-env-vars="GCP_PROJECT_ID=$PROJECT_ID" `
  --set-env-vars="DRIVE_CIRC_DESGLOSADO_FOLDER_ID=1QSDVnsqiYvEJiqB4Mq0iEqtZ4LmLRAQ-" `
  --set-env-vars="DRIVE_CIRC_EJECUTIVO_FOLDER_ID=1lJr02zHsmow9EPOnwUQxPYwScHIUGp1_" `
  --set-secrets="SONDA_QUERY_USER=SONDA_QUERY_USER:latest" `
  --set-secrets="SONDA_QUERY_PASSWORD=SONDA_QUERY_PASSWORD:latest"