# ============================================================
# Setup Cloud Scheduler trigger for pipeline-desinc
# Run once. For schedule changes, use:
#   gcloud scheduler jobs update http pipeline-desinc-daily ...
# ============================================================

$PROJECT_ID = gcloud config get-value project
$REGION     = "us-central1"
$SA_EMAIL   = "mbdataflow-runner@$PROJECT_ID.iam.gserviceaccount.com"

# Enable required API
gcloud services enable cloudscheduler.googleapis.com

# Grant Cloud Run invoker role to the SA
gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member="serviceAccount:$SA_EMAIL" `
  --role="roles/run.invoker"

# Create the scheduler job: 6:00 AM CDMX daily
gcloud scheduler jobs create http pipeline-desinc-daily `
  --location=$REGION `
  --schedule="0 5 * * *" `
  --time-zone="America/Mexico_City" `
  --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/pipeline-desinc:run" `
  --http-method=POST `
  --oauth-service-account-email=$SA_EMAIL `
  --attempt-deadline="30m"

Write-Host "Scheduler created. View with:"
Write-Host "  gcloud scheduler jobs list --location=$REGION"