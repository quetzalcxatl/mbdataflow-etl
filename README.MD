# MBDataFlow_ETL

ETL/EL pipelines for **Metrobús CDMX** operational data, deployed as **Google Cloud Run Jobs**.

> **Status:** Work in progress. Several pipelines are functional; others are under active development.

## Overview

This monorepo contains data pipelines that extract operational data from Metrbús sources, transform it as needed, and load it into **Google BigQuery** and **Google Drive** for analysis and reporting. Each pipeline targets a specific datadomain (vehicle fleet, CAN bus telemetry, operator reports, vehicle decomissioning, etc.) and is desinged to run as an independent Cloud Run Job.

## Tech Stack

- **Python 3.13**
- **Google Cloud Platform** - Cloud Run Jobs, Cloud Scheduler, BigQuery, Secret Manager
- **Google APIs** - Drive, Sheets (via `google-api-python-client`)
- **Data** - `pandas`, `numpy`
- **Web automation** - `selenium`

## Repository Structure

```
MBDataFlow_ETL/
├── config/
│   ├── settings.py              # env vars + _runtime_path() helper
│   └── credentials/             # gitignored
├── extract/
│   ├── base.py                  # Extractor ABC
│   ├── helpers/
│   └── scrapers/
│       └── Desincorporaciones.py
|       └── Circuitos.py   
├── transform/
│   └── transformers/
├── load/
│   ├── base.py                  # Loader ABC
│   └── loaders/
│       └── Desincorporaciones_drive_loader.py
│       └── Circuitos_drive_loader.py
├── pipelines/
│   ├── pipeline_Desinc.py       # entrypoints invocables como python -m
│   ├── pipeline_Circuitos.py
│   ├── pipeline_CanBus.py                  # en pausa
│   └── pipeline_rangofechas_canbus.py      # en pausa
├── utils/
│   ├── dates.py                 # yesterday_cdmx(), today_cdmx() · TZ-aware
│   ├── logger.py
│   └── turno.py
├── scripts/
│   ├── deploy_desinc.ps1        # build + update job
│   ├── deploy_job_desinc.ps1    # crear job (one-time)
│   ├── setup_scheduler_desinc.ps1   
|   ├── deploy_circuitos.ps1         
│   ├── deploy_job_circuitos.ps1     
│   └── setup_scheduler_circuitos.ps1
├── docs/
│   ├── architecture.html        # diagrama visual
│   └── monitoring.md
├── tests/                        # vacío
├── Dockerfile                    # Python 3.13 + Chrome for Testing
├── cloudbuild.yaml
├── requirements.txt              # versiones pineadas
├── .env.example                  # template
└── .env                          # gitignored
```

## Pipelines

| Pipeline | Description | Status |
|---|---|---|
| `pipeline_Desinc` | Vehicle disincorporation | ✅ Functional |
| `pipeline_Circuitos` | Metrobus circuit reports | ✅ Functional |
| Others | Various extractors and transformers | 🚧 In progress |

## Deployment

Pipelines are deployment individualy as Cloud Run Jobs from a shared Docker image. Ci/CD via GitHub Actions and Cloud Build (in progress).

## License

GPL-3.0 — see [LICENSE](LICENSE).

---