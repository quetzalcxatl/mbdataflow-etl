"""Utility helpers for MBDataflow."""

from __future__ import annotations

import os

try:
    from google.cloud import bigquery
except Exception:  # pragma: no cover - dependency may not be installed
    bigquery = None


def upload_dataframe(dataframe, table_id: str) -> None:
    """Upload a pandas DataFrame to BigQuery.

    Parameters
    ----------
    dataframe:
        The DataFrame to upload.
    table_id:
        The destination BigQuery table ID in the format
        ``project.dataset.table``.
    """
    if bigquery is None:
        # This is a development placeholder. In production, ensure that
        # ``google-cloud-bigquery`` is installed and credentials are set.
        print("BigQuery client not available. Skipping upload.")
        return

    # Use default credentials (Workload Identity, ADC, or service account if set)
    try:
        client = bigquery.Client()
        job = client.load_table_from_dataframe(dataframe, table_id)
        job.result()
    except Exception as exc:  # pragma: no cover - network/auth errors
        print(f"BigQuery upload skipped: {exc}")
