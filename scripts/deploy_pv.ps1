# ============================================================
# MBDataFlow_ETL — Deploy pipeline-pv to Cloud Run
#
# Workflow:
#   1. Builds image via Cloud Build, tagged with current commit SHA
#   2. Updates Cloud Run Job to point at the new image (:latest),
#      or CREATES it via deploy_job_pv.ps1 if it doesn't exist yet
#      (so the first deploy is also just: .\scripts\deploy_pv.ps1)
#
# Usage:
#   .\scripts\deploy_pv.ps1            # Deploy current commit
#   .\scripts\deploy_pv.ps1 -Execute   # Also trigger execution after deploy
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

# --- Create or update Job ----------------------------------
# First deploy: the Job doesn't exist yet. It must be created AFTER the build,
# otherwise it would point at an :latest image without pipeline_PV. So this
# script builds first and then delegates creation to deploy_job_pv.ps1 (which
# carries --target,prod and the rest of the Job config). Later deploys update.
$IMAGE = "$REGION-docker.pkg.dev/$PROJECT_ID/mbdataflow/etl-pipelines:latest"

# 'describe' writes to stderr when the Job is missing; under
# ErrorActionPreference=Stop Windows PowerShell would turn that into a
# terminating error, so relax it just for the existence check.
$ErrorActionPreference = "Continue"
gcloud run jobs describe pipeline-pv --region=$REGION --format="value(name)" 2>$null | Out-Null
$jobExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"

if ($jobExists) {
    Write-Host "==> Updating Cloud Run Job to new image" -ForegroundColor Cyan
    gcloud run jobs update pipeline-pv `
      --image=$IMAGE `
      --region=$REGION
} else {
    Write-Host "==> Job 'pipeline-pv' not found: creating it (deploy_job_pv.ps1)" -ForegroundColor Cyan
    & "$PSScriptRoot\deploy_job_pv.ps1"
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Job create/update failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "==> Deploy complete." -ForegroundColor Green
Write-Host "    Image tag: $shortSha (also tagged :latest)"
Write-Host "    Next scheduled run: daily 4:00 PM CDMX (Cloud Scheduler)"


# --- Optional execution ------------------------------------
if ($Execute) {
    Write-Host ""
    Write-Host "==> Executing Job immediately (-Execute flag)" -ForegroundColor Cyan
    gcloud run jobs execute pipeline-pv --region=$REGION
}
