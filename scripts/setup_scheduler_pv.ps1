# ============================================================
# Setup Cloud Scheduler trigger for pipeline-pv
# Run once. For schedule changes, use:
#   gcloud scheduler jobs update http pipeline-pv-daily ...
#
# CUTOVER: the legacy job (mbdataflow_2) also runs daily at 16:00 CDMX with
# WRITE_APPEND. PAUSE ITS SCHEDULER BEFORE running this script, otherwise both
# jobs load the same day and the table gets duplicates whenever the legacy job
# finishes after this one:
#   gcloud scheduler jobs list  --location=us-central1
#   gcloud scheduler jobs pause <legacy-job-name> --location=us-central1
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

# Create the scheduler job: 4:00 PM CDMX every day.
# Same time as the legacy job: the Vespertino slot (16:00:00 in
# config/sonda_pv_config.json) is queryable from then on. No overlap with the
# other Sonda jobs (04:00-07:00 window, Architecture.md §5.10).
gcloud scheduler jobs create http pipeline-pv-daily `
  --location=$REGION `
  --schedule="0 16 * * *" `
  --time-zone="America/Mexico_City" `
  --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/pipeline-pv:run" `
  --http-method=POST `
  --oauth-service-account-email=$SA_EMAIL `
  --attempt-deadline="15m"

Write-Host "Scheduler created. View with:"
Write-Host "  gcloud scheduler jobs list --location=$REGION"
