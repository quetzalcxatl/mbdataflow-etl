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


def last_completed_operational_week_cdmx(now: datetime | None = None) -> tuple:
    """
    Ventana de la última "semana operativa" cerrada de Metrobús, en CDMX.

    La semana operativa NO es lunes 00:00 → domingo 23:59:59; es lunes 03:20 →
    lunes 03:20. La frontera responde al inicio de servicio (~03:20 CDMX),
    de modo que los viajes de la madrugada del lunes cuentan como parte del
    servicio de la semana ANTERIOR.

    Contrato:
      * Retorna (start, end) tz-aware CDMX, ambos con hora 03:20:00 exacta.
      * Intervalo SEMIABIERTO [start, end): `end` es el lunes-03:20 más
        reciente que sea <= `now`; `start = end - 7 días`.
      * Si el pipeline corre lunes ~04:00-05:00, devuelve la semana que
        cerró unas horas antes.
      * Si el pipeline corre lunes ANTES de las 03:20, la última semana
        completada es la anterior — el helper lo maneja retrocediendo 7 días.

    Se pasa `now` solo para tests deterministas; en producción se omite y usa
    la hora actual.

    Nota pytz + CDMX: CDMX no observa DST desde 2022, así que la suma/resta
    de días sobre tz-aware es segura y no requiere `.normalize()`. El pattern
    `CDMX_TZ.localize(naive)` es de todos modos el correcto y el que se usa,
    por consistencia y por si la política de DST cambiara en el futuro.
    """
    if now is None:
        now = datetime.now(CDMX_TZ)
    elif now.tzinfo is None:
        now = CDMX_TZ.localize(now)
    else:
        now = now.astimezone(CDMX_TZ)

    # Lunes 03:20 de la SEMANA CORRIENTE de `now`, como naive → luego localize.
    naive_monday_320 = datetime(now.year, now.month, now.day, 3, 20, 0)
    naive_monday_320 -= timedelta(days=now.weekday())   # weekday(): Mon=0 ... Sun=6
    end = CDMX_TZ.localize(naive_monday_320)

    # Si es lunes pero aún no dan las 03:20, la semana "actual" no ha cerrado.
    if end > now:
        end -= timedelta(days=7)

    start = end - timedelta(days=7)
    return start, end

def last_operational_week_number(now: datetime = None) -> int:
    if now is None:
        now = datetime.now(CDMX_TZ)
    elif now.tzinfo is None:
        now = CDMX_TZ.localize(now)
    else:
        now = now.astimezone(CDMX_TZ)
        
    start, _ = last_completed_operational_week_cdmx(now)
    # 'start' es el lunes-03:20 que da comienzo a la semana vencida -> es el inicio de la semana
    # operativa vencida.
    return start.isocalendar()[1]


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