# load/loaders/_drive_retry.py
# -*- coding: utf-8 -*-
'''
Política de reintentos compartida para las llamadas al Google Drive API.

Extraído de Viaje_drive_loader y Pasos_drive_loader, donde estaba duplicado
literal (deuda anunciada en el docstring de Pasos). La extracción se ejecuta
ahora porque el loader de Desincorporaciones lo necesitaría como tercera copia.

Vive en load/loaders/ y no en utils/ porque depende de googleapiclient:
utils/ es stdlib puro y no debe arrastrar la dependencia de la API de Google.
'''

import time
import warnings

from googleapiclient.errors import HttpError


# HTTP status codes que representan fallos temporales del servidor o rate-limits.
# Google recomienda backoff exponencial para todos ellos.
# Los 4xx que NO están aquí (401, 403, 404, 400) son config incorrecta y no se
# reintentan: reintentar no cambia el desenlace y alarga la agonía.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_transient(exc: Exception) -> bool:
    """True si la excepción representa un fallo transitorio que vale reintentar."""
    if isinstance(exc, HttpError):
        return exc.resp.status in _TRANSIENT_STATUS
    # Errores a nivel red: la petición nunca llegó al servidor o la respuesta
    # nunca volvió. Siempre transitorios.
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


def _with_retry(fn, *, attempts: int = 3, base_delay: float = 2.0):
    """
    Ejecuta `fn()` reintentando SOLO errores transitorios con backoff exponencial
    (2s, 4s, 8s por default).

    - Errores permanentes (4xx que no sean 429): propagan en el primer intento.
    - Errores transitorios: hasta `attempts` intentos; si todos fallan, propaga
      el último.

    No se captura BaseException; solo Exception. Ctrl+C sigue interrumpiendo.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if not _is_transient(exc):
                raise  # permanente → propaga sin reintentar
            if attempt == attempts:
                raise  # agotamos los reintentos
            delay = base_delay * (2 ** (attempt - 1))
            warnings.warn(
                f"Drive: intento {attempt}/{attempts} falló "
                f"({type(exc).__name__}); reintento en {delay:.0f}s",
                RuntimeWarning,
            )
            time.sleep(delay)