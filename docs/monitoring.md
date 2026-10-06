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

### pipeline-pv-failures

**Triggers**: Any failed execution of the `pipeline-pv` Cloud Run Job
(daily 16:00 CDMX, loads `centrodecontrol.Sonda.PV`).

**Metric**: `run.googleapis.com/job/completed_execution_count`

**Filters**:
- `job_name = pipeline-pv`
- `result = failed`

**Threshold**: `count > 0` over 1-minute rolling window.

**Notification**: Email channel.

**Notes**: The Job runs with `--max-retries=1`; a failure that recovers on the
retry does not leave the execution FAILED. Re-running a failed day manually is
safe: the load is delete-then-append per day (`BigQueryDayLoader`), so it
replaces the day instead of duplicating it.

**Recreate**: Cloud Console → Monitoring → Alerting → Create Policy
(same steps as `pipeline-desinc-failures`, changing `job_name`).

## Cost

All alerts and notifications in this project currently fall within
the Google Cloud Operations free tier (50 GiB/month logs, standard
Cloud Run metrics are free).