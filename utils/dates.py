"""Date utilities for ETL pipelines."""

from datetime import datetime, timedelta
import pytz

CDMX_TZ = pytz.timezone("America/Mexico_City")


def last_completed_week_cdmx() -> tuple:
    """
    Returns the most recently completed Monday–Sunday week in CDMX timezone.

    Defined as the last full week strictly before today, regardless of which
    weekday today is. If today is Sunday, the current week is NOT yet complete,
    so we return the previous Mon–Sun pair.

    Returns:
        (monday, sunday) as date objects.

    Examples (assuming CDMX local date):
        Today = Wed 2026-06-24 -> (2026-06-15, 2026-06-21)
        Today = Mon 2026-06-22 -> (2026-06-15, 2026-06-21)
        Today = Sun 2026-06-21 -> (2026-06-08, 2026-06-14)
    """
    cdmx = pytz.timezone("America/Mexico_City")
    today = datetime.now(cdmx).date()

    # weekday(): Mon=0 ... Sun=6
    # Days to subtract to reach the most recent Sunday strictly before today.
    days_back_to_sunday = today.weekday() + 1   # Mon->1, ..., Sat->6, Sun->7
    sunday = today - timedelta(days=days_back_to_sunday)
    monday = sunday - timedelta(days=6)
    return monday, sunday


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