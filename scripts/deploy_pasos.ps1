# ============================================================
# MBDataFlow_ETL — Deploy pipeline-pasos to Cloud Run
#
# Workflow:
#   1. Pre-flight: rama main, sin cambios pendientes, Job existente
#   2. Build de la imagen via Cloud Build, tag = commit SHA + :latest
#   3. Update del Cloud Run Job para apuntar a la nueva imagen
#
# Uso:
#   .\scripts\deploy_pasos.ps1            # Solo deploy
#   .\scripts\deploy_pasos.ps1 -Execute   # Deploy + ejecucion async
#
# NOTA — IMAGEN COMPARTIDA:
#   Este script reconstruye 'etl-pipelines:latest', la MISMA imagen que usan
#   pipeline-desinc y pipeline-circ (monorepo, un solo Dockerfile). Como el
#   build parte siempre de 'main' limpio, el contenido no diverge.
#   PENDIENTE DE VERIFICAR: si Cloud Run pinea ':latest' a un digest en el
#   'jobs update' (esperado) o lo resuelve en cada ejecucion. Comprobar con:
#     gcloud run jobs describe pipeline-desinc --region=us-central1
#   Si muestra un 'sha256:', cada Job esta anclado y no hay efecto cruzado.
# ============================================================

param(
    [switch]$Execute
)

$ErrorActionPreference = "Stop"

$JOB_NAME = "pipeline-pasos"
$REGION   = "us-central1"

# --- Pre-flight checks -------------------------------------
Write-Host "==> Pre-flight checks" -ForegroundColor Cyan

$branch = git rev-parse --abbrev-ref HEAD
if ($branch -ne "main") {
    Write-Host "WARNING: You are on branch '$branch', not 'main'." -ForegroundColor Yellow
    $confirm = Read-Host "Continue anyway? (y/N)"
    if ($confirm -ne "y") { exit 1 }
}

$status = git status --porcelain
if ($status) {
    Write-Host "ERROR: You have uncommitted changes. Commit or stash first." -ForegroundColor Red
    git status --short
    exit 1
}

$PROJECT_ID = gcloud config get-value project
$shortSha   = git rev-parse --short HEAD

# El Job debe existir (creado una sola vez por deploy_job_pasos.ps1).
# Sin este check, un typo en $JOB_NAME desperdicia un build de 3 minutos
# antes de fallar en el update.
gcloud run jobs describe $JOB_NAME --region=$REGION --format="value(metadata.name)" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: El Job '$JOB_NAME' no existe en $REGION." -ForegroundColor Red
    Write-Host "       Crearlo primero con: .\scripts\deploy_job_pasos.ps1"
    exit 1
}

Write-Host "    Project:  $PROJECT_ID"
Write-Host "    Region:   $REGION"
Write-Host "    Job:      $JOB_NAME"
Write-Host "    Commit:   $shortSha"
Write-Host ""

# --- Build -------------------------------------------------
Write-Host "==> Building image (this takes ~2-3 minutes)" -ForegroundColor Cyan
gcloud builds submit `
  --config=cloudbuild.yaml `
  --substitutions=_SHA=$shortSha `
  .

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Build failed." -ForegroundColor Red
    exit 1
}

# --- Update Job --------------------------------------------
Write-Host "==> Updating Cloud Run Job to new image" -ForegroundColor Cyan
$IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/mbdataflow/etl-pipelines:latest"

gcloud run jobs update $JOB_NAME `
  --image=$IMAGE `
  --region=$REGION

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Job update failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "==> Deploy complete." -ForegroundColor Green
Write-Host "    Image tag: $shortSha (also tagged :latest)"
Write-Host "    Scheduler: NO configurado aun — ejecucion manual unicamente."

# --- Optional execution ------------------------------------
# --async: el scrape de 10 lineas tarda mucho mas que Desinc. Bloquear la
# terminal por ~20 min es fragil; se sigue por logs.
if ($Execute) {
    Write-Host ""
    Write-Host "==> Lanzando ejecucion (async)" -ForegroundColor Cyan
    gcloud run jobs execute $JOB_NAME --region=$REGION --async

    Write-Host ""
    Write-Host "    Seguir la ejecucion con:" -ForegroundColor Cyan
    Write-Host "      gcloud run jobs executions list --job=$JOB_NAME --region=$REGION --limit=1"
    Write-Host "      gcloud beta run jobs executions logs tail <EXECUTION_NAME> --region=$REGION"
}