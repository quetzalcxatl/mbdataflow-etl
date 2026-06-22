# ============================================================
# MBDataFlow_ETL — Deploy pipeline-desinc to Cloud Run
#
# Workflow:
#   1. Builds image via Cloud Build, tagged with current commit SHA
#   2. Updates Cloud Run Job to point at the new image (:latest)
#
# Usage:
#   .\scripts\deploy_desinc.ps1            # Deploy current commit
#   .\scripts\deploy_desinc.ps1 -Execute   # Also trigger execution after deploy
# ============================================================

param(
    [switch]$Execute
)

$ErrorActionPreference = "Stop"

# --- Pre-flight checks -------------------------------------
Write-Host "==> Pre-flight checks" -ForegroundColor Cyan

# Confirm we're on main and synced
$branch = git rev-parse --abbrev-ref HEAD
if ($branch -ne "main") {
    Write-Host "WARNING: You are on branch '$branch', not 'main'." -ForegroundColor Yellow
    $confirm = Read-Host "Continue anyway? (y/N)"
    if ($confirm -ne "y") { exit 1 }
}

# Confirm no uncommitted changes
$status = git status --porcelain
if ($status) {
    Write-Host "ERROR: You have uncommitted changes. Commit or stash first." -ForegroundColor Red
    git status --short
    exit 1
}

# Confirm gcloud project
$PROJECT_ID = gcloud config get-value project
$REGION     = "us-central1"
$shortSha   = git rev-parse --short HEAD

Write-Host "    Project:  $PROJECT_ID"
Write-Host "    Region:   $REGION"
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

gcloud run jobs update pipeline-desinc `
  --image=$IMAGE `
  --region=$REGION

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Job update failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "==> Deploy complete." -ForegroundColor Green
Write-Host "    Image tag: $shortSha (also tagged :latest)"
<<<<<<< HEAD
Write-Host "    Next scheduled run: 5:00 AM CDMX (Cloud Scheduler)"
=======
Write-Host "    Next scheduled run: 6:00 AM CDMX (Cloud Scheduler)"
>>>>>>> 56636d3e7d46d0f85d4ee6b151503c9b3569eaa1

# --- Optional execution ------------------------------------
if ($Execute) {
    Write-Host ""
    Write-Host "==> Executing Job immediately (-Execute flag)" -ForegroundColor Cyan
    gcloud run jobs execute pipeline-desinc --region=$REGION
}