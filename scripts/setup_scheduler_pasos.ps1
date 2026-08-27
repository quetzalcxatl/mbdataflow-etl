# ============================================================
# MBDataFlow_ETL — Cloud Scheduler trigger para pipeline-pasos
#
# Ejecutar UNA sola vez. Para cambios de horario:
#   gcloud scheduler jobs update http pipeline-pasos-weekly ...
#
# HORARIO: lunes 06:00 CDMX, semanal.
#
#   Por que martes y no lunes:
#     La semana operativa cierra lunes 03:20. Correr el martes devuelve la
#     MISMA ventana (verificado contra last_completed_operational_week_cdmx)
#     pero con >24h de margen para que Sonda consolide, en vez de 2:40h.
#
#   Por que las 06:00 — EXCLUSION MUTUA CONTRA SONDA:
#     La Central de Descargas es por CUENTA, no por reporte. Si otro pipeline
#     encola una solicitud mientras Pasos pollea, Pasos descarga el archivo
#     equivocado SIN lanzar excepcion. Fallo silencioso.
#       pipeline-desinc : diario 05:00, ~3 min  -> 60 min de margen
#       pipeline-circ   : semanal 07:00, lunes
#     Antes de cambiar este horario, revisar:
#       gcloud scheduler jobs list --location=us-central1
# ============================================================

$ErrorActionPreference = "Stop"

$PROJECT_ID = gcloud config get-value project
$REGION     = "us-central1"
$SA_EMAIL   = "mbdataflow-runner@$PROJECT_ID.iam.gserviceaccount.com"
$JOB_NAME   = "pipeline-pasos"
$SCHED_NAME = "pipeline-pasos-weekly"

# El Job debe existir antes de programarlo.
gcloud run jobs describe $JOB_NAME --region=$REGION --format="value(metadata.name)" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: El Job '$JOB_NAME' no existe. Correr deploy_job_pasos.ps1." -ForegroundColor Red
    exit 1
}

# --- Operacion desatendida: habilitar un reintento -----------
# En PR-2 quedo en 0 para que la primera corrida manual fuera limpia.
# Desatendido, un blip de red no deberia costar una semana de datos.
# El loader es idempotente por nombre: el reintento no duplica en Drive.
Write-Host "==> Habilitando 1 reintento en el Job" -ForegroundColor Cyan
gcloud run jobs update $JOB_NAME `
  --region=$REGION `
  --max-retries=1

# --- API y permisos (idempotentes; ya aplicados via Desinc) --
gcloud services enable cloudscheduler.googleapis.com

gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member="serviceAccount:$SA_EMAIL" `
  --role="roles/run.invoker"

# --- Scheduler ----------------------------------------------
# --max-retry-attempts=1: la llamada a jobs:run es asincrona y retorna en
# segundos. Un reintento del SCHEDULER sobre una llamada que si llego pero
# cuya respuesta se perdio lanzaria una segunda Execution en paralelo — dos
# scrapes concurrentes contra la misma cuenta Sonda, justo el escenario que
# el horario esta evitando. Se acota a un solo reintento.
#
# --attempt-deadline: aplica a la llamada HTTP, NO a la duracion del Job.
# 3 min sobra para un POST que responde en segundos.
Write-Host "==> Creando Cloud Scheduler job" -ForegroundColor Cyan
gcloud scheduler jobs create http $SCHED_NAME `
  --location=$REGION `
  --schedule="0 6 * * 1" `
  --time-zone="America/Mexico_City" `
  --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/${JOB_NAME}:run" `
  --http-method=POST `
  --oauth-service-account-email=$SA_EMAIL `
  --attempt-deadline="3m" `
  --max-retry-attempts=1 `
  --description="pipeline_Pasos semanal — lunes 06:00 CDMX. Ver exclusion mutua Sonda en el script."

Write-Host ""
Write-Host "==> Scheduler '$SCHED_NAME' creado." -ForegroundColor Green
Write-Host "    Verificar con:"
Write-Host "      gcloud scheduler jobs list --location=$REGION"
Write-Host "    Disparo manual de prueba (NO espera al lunes):"
Write-Host "      gcloud scheduler jobs run $SCHED_NAME --location=$REGION"