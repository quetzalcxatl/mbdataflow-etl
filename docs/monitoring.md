# Monitoring & Alerting

## Alert Policies

### pipeline-desinc-failures

**Triggers**: Any failed execution of the `pipeline-desinc` Cloud Run Job.

**Metric**: `run.googleapis.com/job/completed_execution_count`

**Filters**:
- `job_name = pipeline-desinc`
- `result = failed`

**Threshold**: `count > 0` over 1-minute rolling window.

**Notification**: Email channel.

**Recreate**: Cloud Console → Monitoring → Alerting → Create Policy
(See repository setup notes for step-by-step.)

## Cost

All alerts and notifications in this project currently fall within
the Google Cloud Operations free tier (50 GiB/month logs, standard
Cloud Run metrics are free).