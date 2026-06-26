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

MBDataFlow_ETL/
├── extract/        # Scrapers and extraction helpers
├── transform/      # Data transformation logic
├── load/           # BigQuery and Drive loaders
├── pipelines/      # Pipeline orchestrators (one per data domain)
├── config/         # Settings and credentials (gitignored)
├── utils/          # Shared utilities
└── tests/          # Unit and integration tests

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