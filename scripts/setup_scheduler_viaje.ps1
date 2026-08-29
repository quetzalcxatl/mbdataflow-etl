# ============================================================
# Setup Cloud Scheduler trigger for pipeline-viaje
# Run once. For schedule changes, use:
#   gcloud scheduler jobs update http pipeline-viaje-weekly ...
#
# Alert policy for job failures is created MANUALLY via the Cloud
# Monitoring UI — see docs/monitoring.md.
# ============================================================

$PROJECT_ID = gcloud config get-value project
$REGION     = "us-central1"
$SA_EMAIL   = "mbdataflow-runner@$PROJECT_ID.iam.gserviceaccount.com"

# Enable required API (idempotent — safe even if other pipelines already enabled it)
gcloud services enable cloudscheduler.googleapis.com

# Grant Cloud Run invoker role to the SA (idempotent)
gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member="serviceAccount:$SA_EMAIL" `
  --role="roles/run.invoker"

# Create the scheduler job: 5:00 AM CDMX every Monday.
# The trigger runs 1h40m after the operational-week boundary (Mon 03:20 CDMX),
# giving Sonda enough time to finalize the Sunday-night data that crosses into
# early Monday and now belongs to the closing operational week.
gcloud scheduler jobs create http pipeline-viaje-weekly `
  --location=$REGION `
  --schedule="0 4 * * 1" `
  --time-zone="America/Mexico_City" `
  --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/pipeline-viaje:run" `
  --http-method=POST `
  --oauth-service-account-email=$SA_EMAIL `
  --attempt-deadline="35m"

Write-Host "Scheduler created. View with:"
Write-Host "  gcloud scheduler jobs list --location=$REGION"