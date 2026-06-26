# ============================================================
# Setup Cloud Scheduler trigger for pipeline-circuitos
# Run once. For schedule changes, use:
#   gcloud scheduler jobs update http pipeline-circuitos-weekly ...
# ============================================================

$PROJECT_ID = gcloud config get-value project
$REGION     = "us-central1"
$SA_EMAIL   = "mbdataflow-runner@$PROJECT_ID.iam.gserviceaccount.com"

# Enable required API (idempotent — safe even if Desinc already enabled it)
gcloud services enable cloudscheduler.googleapis.com

# Grant Cloud Run invoker role to the SA (idempotent)
gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member="serviceAccount:$SA_EMAIL" `
  --role="roles/run.invoker"

# Create the scheduler job: 7:00 AM CDMX every Monday
gcloud scheduler jobs create http pipeline-circuitos-weekly `
  --location=$REGION `
  --schedule="0 7 * * 1" `
  --time-zone="America/Mexico_City" `
  --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/pipeline-circuitos:run" `
  --http-method=POST `
  --oauth-service-account-email=$SA_EMAIL `
  --attempt-deadline="30m"

Write-Host "Scheduler created. View with:"
Write-Host "  gcloud scheduler jobs list --location=$REGION"