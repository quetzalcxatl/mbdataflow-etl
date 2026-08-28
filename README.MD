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
│   ├── credentials/...
│   ├── __init__.py
│   ├── settings.py
│   └── sonda_pv_config.json
├── docs/
│   ├── decision_log.md
│   └── monitoring.md
├── env/...
│   ├── Scripts/...
│   ├── .gitignore
│   └── pyvenv.cfg
├── extract/
│   ├── helpers/
│   │   ├── download_helper.py
│   │   └── intervalo_fecha_helper.py
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── CanBus.py
│   │   ├── Circuitos.py
│   │   ├── Desincorporaciones.py
│   │   ├── FlotaVehicular.py
│   │   ├── Pasos.py
│   │   ├── recover_sonda_pv.py
│   │   ├── Reporte_Viaje.py
│   │   └── Reportes_Operador.py
│   ├── __init__.py
│   └── base.py
├── load/
│   ├── loaders/
│   │   ├── BigQuery_loader.py
│   │   ├── CAN_drive_loader.py
│   │   ├── Circuitos_drive_loader.py
│   │   ├── debug_drive_loader.py
│   │   ├── Desincorporaciones_drive_loader.py
│   │   ├── google_drive_loader.py
│   │   ├── Pasos_drive_loader.py
│   │   ├── Reportes_Operador_drive_loader.py
│   │   └── Viaje_drive_loader.py
│   ├── schemas/
│   │   └── viaje.py
│   ├── __init__.py
│   └── base.py
├── logs/
├── pipelines/
│   ├── __init__.py
│   ├── pipeline_CanBus.py
│   ├── pipeline_Circuitos.py
│   ├── pipeline_Desinc.py
│   ├── pipeline_Pasos.py
│   ├── pipeline_rangofechas_canbus.py
│   └── pipeline_Viaje.py
├── scripts/
│   ├── audit_viaje_parity.py
│   ├── deploy_circuitos.ps1
│   ├── deploy_desinc.ps1
│   ├── deploy_job_circuitos.ps1
│   ├── deploy_job_desinc.ps1
│   ├── deploy_job_pasos.ps1
│   ├── deploy_job_viaje.ps1
│   ├── deploy_pasos.ps1
│   ├── deploy_viaje.ps1
│   ├── setup_scheduler_circuitos.ps1
│   ├── setup_scheduler_desinc.ps1
│   ├── setup_scheduler_pasos.ps1
│   ├── setup_scheduler_viaje.ps1
│   ├── smoke_test_bigquery_loader.py
│   └── smoketest_bq_sql_runner.py
├── tests/
│   ├── test_extract/
│   ├── test_load/
│   └── test_transform/
├── transform/
│   ├── sql/
│   │   └── intervalos_dinamicos.sql
│   ├── transformers/
│   │   ├── __init__.py
│   │   ├── CanBus.py
│   │   ├── FlotaVehicular.py
│   │   └── Reporte_Viaje.py
│   ├── __init__.py
│   ├── base.py
│   └── bq_sql_runner.py
├── utils/
│   ├── __init__.py
│   ├── dates.py
│   ├── logger.py
│   └── turno.py
├── .dockerignore
├── .env.example
├── .gcloudignore
├── .gitignore
├── Architecture.md
├── cloudbuild.yaml
├── Dockerfile
├── LICENSE
├── README.MD
└── requirements.txt
```

## Pipelines

| Pipeline | Tipo | Estado | Trigger |
|---|---|---|---|
| `pipeline_Desinc` | EL | Producción | Cloud Scheduler diario · 5:00 AM CDMX |
| `pipeline_Circuitos` | EL | Producción | Cloud Scheduler semanal · 7:00 AM CDMX|
| `pipeline_CanBus` | EL | Pausado · calidad de datos upstream | — |
| `pipeline_rangofechas_canbus` | EL | Pausado · calidad de datos upstream | — |
| `pipeline_Pasos` | EL | Producción | Cloud Scheduler semanal · 6:00 AM CDMX |
| `pipeline_Viaje` | ETL-T | Local | - | 

## Deployment

Pipelines are deployment individualy as Cloud Run Jobs from a shared Docker image. Ci/CD via GitHub Actions and Cloud Build (in progress).

## License

GPL-3.0 — see [LICENSE](LICENSE).

---