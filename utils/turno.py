# utils/truno.py
from datetime import datetime

def get_turno(now: datetime = None) -> str:
    """Determina el tunro basado en la hora actual."""
    if now is None:
        now = datetime.now()
    if 4 <= now.hour < 12:
        return "Matutino"
    if 12 <= now.hour <= 23 or now.hour == 0:
        return "Vespertino"
    return "Ninguno"


