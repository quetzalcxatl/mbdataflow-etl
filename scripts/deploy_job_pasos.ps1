# ============================================================
# MBDataFlow_ETL — Create Cloud Run Job for pipeline_Pasos
#
# Ejecutar UNA sola vez para crear el Job.
# Para actualizar la imagen, usar: .\scripts\deploy_pasos.ps1
#
# Dimensionamiento (ver PR-2):
#   task-timeout 2h  -> por encima del techo interno del scraper
#                       (10 lineas x (status_timeout 600s + file 120s) ~= 127 min)
#                       para que el TimeoutError del codigo gane a la
#                       infraestructura y el log sea accionable.
#   memory     2Gi   -> Chrome vive ~6-25 min con 10 ciclos de iframe.
#                       Un OOM es exit 137 sin traceback. Revisable a la baja
#                       tras observar el consumo real en Cloud Monitoring.
#   max-retries  0   -> primera operacion manual: un fallo es un fallo.
# ============================================================

$ErrorActionPreference = "Stop"

$PROJECT_ID = gcloud config get-value project
$REGION     = "us-central1"
$SA_EMAIL   = "mbdataflow-runner@$PROJECT_ID.iam.gserviceaccount.com"
$IMAGE      = "$REGION-docker.pkg.dev/$PROJECT_ID/mbdataflow/etl-pipelines:latest"

# RELLENAR: ID de la carpeta raiz 'reporte_de_pasos' en la unidad compartida.
$DRIVE_PASOS_FOLDER_ID = "1eESv5stz6GoZyJbEH5TTgffkUhWjDC1u"

if ($DRIVE_PASOS_FOLDER_ID -eq "<PENDIENTE>") {
    Write-Host "ERROR: falta DRIVE_PASOS_FOLDER_ID en el script." -ForegroundColor Red
    exit 1
}

gcloud run jobs create pipeline-pasos `
  --image=$IMAGE `
  --command=python `
  --args="-m,pipelines.pipeline_Pasos" `
  --service-account=$SA_EMAIL `
  --region=$REGION `
  --max-retries=0 `
  --task-timeout=2h `
  --memory=2Gi `
  --cpu=1 `
  --set-env-vars="GCP_PROJECT_ID=$PROJECT_ID,DRIVE_PASOS_FOLDER_ID=$DRIVE_PASOS_FOLDER_ID" `
  --set-secrets="SONDA_QUERY_USER=SONDA_QUERY_USER:latest,SONDA_QUERY_PASSWORD=SONDA_QUERY_PASSWORD:latest"

Write-Host ""
Write-Host "==> Job 'pipeline-pasos' creado." -ForegroundColor Green
Write-Host "    Verificar con:"
Write-Host "      gcloud run jobs describe pipeline-pasos --region=$REGION"