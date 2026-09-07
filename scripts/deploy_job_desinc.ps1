# ============================================================
# MBDataFlow_ETL — Create Cloud Run Job for pipeline_Desinc
# Run once to create the Job. For updates, use deploy_update.ps1
# ============================================================

# Notas de configuración:
#   * Dos carpetas de Drive HERMANAS (no anidadas): una por servicio del
#     campo 'Servicio' del formulario. IDs planos, sin jerarquía año/semana.
#   * task-timeout=30m — la corrida local de los dos ciclos toma ~35s.
#     Margen amplio y deliberado; Cloud Run headless es más lento pero no
#     en un orden de magnitud.
#   * Los flags --set-env-vars repetidos ACUMULAN en `jobs create`; no se
#     sobrescriben. Verificado contra el Job en producción.

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
  --set-env-vars="DRIVE_APOYO_FOLDER_ID=1ufs8r4gJOXk_SdIgq7HmyG59TgWeVHsr" `
  --set-secrets="SONDA_QUERY_USER=SONDA_QUERY_USER:latest" `
  --set-secrets="SONDA_QUERY_PASSWORD=SONDA_QUERY_PASSWORD:latest"