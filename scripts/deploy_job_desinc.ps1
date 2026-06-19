# ============================================================
# MBDataFlow_ETL — Create Cloud Run Job for pipeline_Desinc
# Run once to create the Job. For updates, use deploy_update.ps1
# ============================================================

$PROJECT_ID  = gcloud config get-value project
$REGION      = "us-central1"
$SA_EMAIL    = "mbdataflow-runner@$PROJECT_ID.iam.gserviceaccount.com"
$IMAGE       = "$REGION-docker.pkg.dev/$PROJECT_ID/mbdataflow/etl-pipelines:latest"

gcloud run jobs create pipeline-desinc `
  --image=$IMAGE `
  --command=python `
  --args="-m,pipelines.pipeline_Desinc" `
  --service-account=$SA_EMAIL `
  --region=$REGION `
  --max-retries=1 `
  --task-timeout=30m `
  --memory=1Gi `
  --cpu=1 `
  --set-env-vars="GCP_PROJECT_ID=$PROJECT_ID" `
  --set-env-vars="DRIVE_DESINC_FOLDER_ID=1Uq9YevFoszT7f5tRebV_ePRbqPnKr7ol" `
  --set-secrets="SONDA_QUERY_USER=SONDA_QUERY_USER:latest" `
  --set-secrets="SONDA_QUERY_PASSWORD=SONDA_QUERY_PASSWORD:latest"