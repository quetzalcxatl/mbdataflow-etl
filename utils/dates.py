"""Date utilities for ETL pipelines."""

from datetime import datetime, timedelta
import pytz

CDMX_TZ = pytz.timezone("America/Mexico_City")


def yesterday_cdmx() -> datetime:
    """Return yesterday's date in CDMX timezone.

    Used as the "data date" for pipelines that ingest the previous
    day's complete records (e.g. Desincorporaciones).

    Timezone-aware: works correctly regardless of where Python runs
    (local machine in any TZ, or Cloud Run in UTC).
    """
    return datetime.now(CDMX_TZ) - timedelta(days=1)


def today_cdmx() -> datetime:
    """Return today's date in CDMX timezone."""
    return datetime.now(CDMX_TZ)