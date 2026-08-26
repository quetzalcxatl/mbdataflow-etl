# load/loaders/Pasos_drive_loader.py
# -*- coding: utf-8 -*-
'''
Helper: Google Drive loader para los CSV del reporte de Pasos por parada.

A diferencia de Viaje —que sube un único archivo a un folder ID fijo— Pasos
produce 10 CSV por corrida (uno por línea operativa) que aterrizan todos en la
MISMA carpeta de destino, resuelta en runtime siguiendo la jerarquía:

    reporte_de_pasos/          <- root, ID fijo por env var
    └── <año>/                 <- p.ej. '2026'
        └── <semana>/          <- p.ej. '01'
            ├── pasos_L1_sem01_...csv
            └── ... (10 archivos)

El año y la semana llegan por CONSTRUCTOR desde el orquestador, que ya los
conoce vía utils.dates. Este loader NO los deriva parseando nombres de archivo:
la fuente de verdad es el argumento, no la convención de naming.

MEDULAR: la carga es camino crítico. Política de errores BEST-EFFORT CON RAISE
FINAL: se intentan los 10 archivos, se acumulan los fallos, y si hubo al menos
uno se lanza RuntimeError al final. El Job queda FAILED (dispara la alerta de
Cloud Monitoring) pero los archivos que sí pudieron subir quedan subidos —
la re-corrida solo tiene que atacar los que faltan. Ver `run()`.

DEUDA CONOCIDA: `_is_transient` / `_with_retry` están duplicados desde
`Viaje_drive_loader.py`. Extraer a un módulo compartido es un refactor
deliberadamente fuera del scope de estos PRs.
'''

import time
import warnings
from pathlib import Path

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors    import HttpError
from googleapiclient.http      import MediaFileUpload

from utils.logger import ok, info, err


# ------------------------------------------------------------------
# Constantes de módulo
# ------------------------------------------------------------------

# Scope COMPLETO, no 'drive.file'.
#
# 'drive.file' limita el acceso a archivos creados por la propia app. Este
# loader necesita localizar carpetas creadas manualmente por un humano en la
# unidad compartida (la raíz 'reporte_de_pasos' y las de año/semana si ya
# existen). Verificado empíricamente en PR-A: con este scope la SA las ve.
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

FOLDER_MIME = "application/vnd.google-apps.folder"
CSV_MIME    = "text/csv"

# HTTP status codes que representan fallos temporales del servidor o rate-limits.
# Los 4xx que NO están aquí (401, 403, 404, 400) son config incorrecta y no se
# reintentan: reintentar no cambia el desenlace.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


# ------------------------------------------------------------------
# Convención de nombres de carpeta
# ------------------------------------------------------------------
#
# CRÍTICO: estos nombres deben coincidir EXACTAMENTE con los de las carpetas ya
# existentes en Drive. Drive permite hermanos con nombre duplicado, así que un
# desajuste ('01' vs '1') no lanza error — crea un árbol paralelo en silencio.

def _year_folder_name(year: int) -> str:
    """Nombre de la carpeta de año. Convención: '2026', '2027', ..."""
    return f"{year:04d}"


def _week_folder_name(week_number: int) -> str:
    """Nombre de la carpeta de semana. Convención: '01', '02', ..., '53'.

    Si las carpetas existentes en Drive resultan estar SIN padding ('1', '2'),
    cambiar a `str(week_number)` — y solo aquí.
    """
    return f"{week_number:02d}"


# ------------------------------------------------------------------
# Política de reintentos para llamadas al Drive API
# (duplicado de Viaje_drive_loader — ver DEUDA CONOCIDA arriba)
# ------------------------------------------------------------------

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


def _escape_q(value: str) -> str:
    """Escapa un literal para interpolarlo en una query de Drive API.

    Aquí SÍ importa: los nombres de archivo vienen del scraper, no son
    numéricos, y un apóstrofo rompería la query de forma opaca.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


# ==================================================================
# Loader
# ==================================================================

class Pasos_load_to_drive:
    """
    Sube los CSV de Pasos ya descargados a la jerarquía año/semana en Drive.

    Contrato de construcción:
      file_paths     — los Path que retornó `Pasos_Scraper.scrape()`.
      root_folder_id — ID de la carpeta 'reporte_de_pasos' en la unidad
                       compartida. Debe existir; este loader NO la crea (crear
                       la raíz enmascararía un folder ID mal configurado).
      year           — año calendario de la semana operativa.
      week_number    — número de semana operativa (1..53).
    """

    def __init__(
        self,
        file_paths: list[Path],
        root_folder_id: str,
        year: int,
        week_number: int,
    ):
        if not root_folder_id:
            raise ValueError("root_folder_id vacío — revisa DRIVE_PASOS_FOLDER_ID")
        if not 1 <= week_number <= 53:
            raise ValueError(f"week_number fuera de rango [1,53]: {week_number}")

        self.file_paths = [Path(p) for p in file_paths]
        self.root_folder_id = root_folder_id
        self.year = int(year)
        self.week_number = int(week_number)

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------

    def _get_drive_service(self):
        # Sin retry: un fallo aquí es config (credenciales/scopes), no red.
        creds, _ = google.auth.default(scopes=DRIVE_SCOPES)
        return build("drive", "v3", credentials=creds)

    # ------------------------------------------------------------------
    # Resolución idempotente de carpetas
    # ------------------------------------------------------------------

    def _find_folder(self, service, name: str, parent_id: str) -> str | None:
        """Devuelve el ID de la subcarpeta `name` bajo `parent_id`, o None.

        Si hay más de un match (hermanos con nombre duplicado, que Drive
        permite), devuelve el primero y avisa: es un síntoma de que alguien
        creó una carpeta a mano con la misma etiqueta, o de que la convención
        de nombres cambió a mitad de camino.
        """
        query = (
            f"name = '{_escape_q(name)}' "
            f"and '{parent_id}' in parents "
            f"and mimeType = '{FOLDER_MIME}' "
            f"and trashed = false"
        )
        response = _with_retry(lambda: (
            service.files()
            .list(
                q=query,
                fields="files(id, name)",
                spaces="drive",
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        ))
        folders = response.get("files", [])
        if not folders:
            return None
        if len(folders) > 1:
            warnings.warn(
                f"Drive: {len(folders)} carpetas llamadas '{name}' bajo "
                f"{parent_id}. Se usa la primera ({folders[0]['id']}). "
                f"Revisa duplicados en la unidad compartida.",
                RuntimeWarning,
            )
        return folders[0]["id"]

    def _create_folder(self, service, name: str, parent_id: str) -> str:
        """Crea la subcarpeta `name` bajo `parent_id` y devuelve su ID."""
        metadata = {
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        }
        created = _with_retry(lambda: (
            service.files()
            .create(
                body=metadata,
                fields="id, name",
                supportsAllDrives=True,
            )
            .execute()
        ))
        return created["id"]

    def _get_or_create_folder(self, service, name: str, parent_id: str) -> str:
        """Idempotente: devuelve el ID de `name` bajo `parent_id`, creándola
        si hace falta.

        NO es atómico. Dos corridas concurrentes podrían crear dos carpetas
        homónimas. Aceptable: el Job es único y disparado por Cloud Scheduler
        una vez por semana. Si algún día hay concurrencia real, esto necesita
        un re-check post-create o un lock externo.
        """
        existing = self._find_folder(service, name, parent_id)
        if existing:
            info(f"Carpeta '{name}' ya existe (ID: {existing})")
            return existing

        created_id = self._create_folder(service, name, parent_id)
        ok(f"Carpeta '{name}' creada (ID: {created_id})")
        return created_id

    def _resolve_week_folder(self, service) -> str:
        """Encadena root → año → semana. Devuelve el ID del folder de semana,
        que es donde aterrizan los 10 CSV.
        """
        year_name = _year_folder_name(self.year)
        week_name = _week_folder_name(self.week_number)

        year_id = self._get_or_create_folder(service, year_name, self.root_folder_id)
        week_id = self._get_or_create_folder(service, week_name, year_id)

        info(f"Destino resuelto: {year_name}/{week_name} (ID: {week_id})")
        return week_id

    # ------------------------------------------------------------------
    # Idempotencia a nivel archivo
    # ------------------------------------------------------------------

    def _file_already_exists(self, service, filename: str,
                             parent_folder_id: str) -> str | None:
        """Devuelve el ID del archivo `filename` bajo `parent_folder_id`, o None.

        Idempotencia POR NOMBRE, igual que Viaje. Consecuencia deliberada: si
        re-generas un CSV con el mismo nombre pero contenido corregido, este
        loader lo OMITE en vez de reemplazarlo. Para forzar el reemplazo hay que
        borrar el archivo en Drive a mano. Se elige así porque el caso frecuente
        es la re-corrida idéntica (retry del Job), no la corrección de contenido.
        """
        query = (
            f"name = '{_escape_q(filename)}' "
            f"and '{parent_folder_id}' in parents "
            f"and mimeType != '{FOLDER_MIME}' "
            f"and trashed = false"
        )
        response = _with_retry(lambda: (
            service.files()
            .list(
                q=query,
                fields="files(id, name)",
                spaces="drive",
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        ))
        files = response.get("files", [])
        return files[0]["id"] if files else None

    # ------------------------------------------------------------------
    # Subida de un archivo
    # ------------------------------------------------------------------

    def _upload_one(self, service, file_path: Path,
                    week_folder_id: str) -> tuple[str, bool]:
        """Sube `file_path` a `week_folder_id`.

        Retorna (file_id, skipped) donde skipped=True significa que el archivo
        ya estaba en Drive y no se re-subió.

        Levanta FileNotFoundError si el path local no existe. El llamador decide
        si eso aborta la corrida o solo cuenta como un fallo más.
        """
        if not file_path.exists():
            raise FileNotFoundError(f"No existe el archivo local: {file_path}")

        filename = file_path.name

        existing_id = self._file_already_exists(service, filename, week_folder_id)
        if existing_id:
            info(f"'{filename}' ya existe en Drive — subida omitida. "
                 f"(ID: {existing_id})")
            return existing_id, True

        # NOTA: MediaFileUpload(resumable=True) ya reintenta chunks internamente
        # DURANTE la subida en curso, pero un 5xx en el commit final del upload
        # no lo reintenta desde adentro — ese es el hueco que _with_retry tapa.
        metadata = {"name": filename, "parents": [week_folder_id]}
        media = MediaFileUpload(str(file_path), mimetype=CSV_MIME, resumable=True)

        uploaded = _with_retry(lambda: (
            service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id, name",
                supportsAllDrives=True,
            )
            .execute()
        ))
        ok(f"'{filename}' subido exitosamente. (ID: {uploaded['id']})")
        return uploaded["id"], False

    # ------------------------------------------------------------------
    # Punto de entrada público
    # ------------------------------------------------------------------

    def run(self) -> dict[Path, str]:
        """
        Resuelve la carpeta año/semana y sube todos los `file_paths` ahí.

        Retorna un dict {Path local: file ID en Drive} con los archivos que
        quedaron en Drive — tanto los subidos en esta corrida como los omitidos
        por ya existir. Los fallidos NO aparecen en el dict.

        BEST-EFFORT CON RAISE FINAL: un fallo en el archivo N no interrumpe los
        N+1..10. Al terminar, si hubo al menos un fallo, se lanza RuntimeError
        con el resumen. Eso deja el Cloud Run Job en FAILED —contrato con la
        alerta de Cloud Monitoring— sin desperdiciar el trabajo que sí salió.

        Una lista vacía es un fallo, no un no-op: significa que el scraper no
        produjo nada y nadie se enteró.
        """
        if not self.file_paths:
            raise ValueError(
                "file_paths vacío — el scraper no produjo archivos. "
                "Esto es un fallo del extract, no una corrida sin trabajo."
            )

        service = self._get_drive_service()
        week_folder_id = self._resolve_week_folder(service)

        results: dict[Path, str] = {}
        failures: list[tuple[Path, Exception]] = []
        skipped_count = 0

        total = len(self.file_paths)
        for idx, path in enumerate(self.file_paths, start=1):
            print(f"[{idx}/{total}] {path.name}")
            try:
                file_id, skipped = self._upload_one(service, path, week_folder_id)
                results[path] = file_id
                if skipped:
                    skipped_count += 1
            except Exception as exc:
                err(f"'{path.name}' falló: {type(exc).__name__}: {exc}")
                failures.append((path, exc))

        uploaded_count = len(results) - skipped_count

        print(f"\n  📋  RESUMEN DE CARGA — {_year_folder_name(self.year)}/"
              f"{_week_folder_name(self.week_number)}")
        print(f"  {'─'*45}")
        print(f"  Subidos   : {uploaded_count}")
        print(f"  Omitidos  : {skipped_count}  (ya existían en Drive)")
        print(f"  Fallidos  : {len(failures)}")
        for path, exc in failures:
            print(f"    ❌  {path.name}  —  {type(exc).__name__}: {exc}")

        if failures:
            detalle = "; ".join(
                f"{p.name}: {type(e).__name__}" for p, e in failures
            )
            raise RuntimeError(
                f"Carga a Drive incompleta: {len(failures)}/{total} archivos "
                f"fallaron. Detalle: {detalle}"
            )

        return results


# ------------------------------------------------------------------
# Test aislado
# ------------------------------------------------------------------
#
# python -m load.loaders.Pasos_drive_loader
#
# Toma del directorio raw los CSV de Pasos que haya en disco y los sube a la
# carpeta año/semana correspondiente. Correr DOS veces:
#   1ra — todos "subidos".
#   2da — todos "omitidos", mismo dict de IDs, exit code 0.

if __name__ == "__main__":
    from config.settings import RAW_PASOS_PATH, DRIVE_PASOS_FOLDER_ID
    from utils.dates import (
        last_completed_operational_week_cdmx,
        last_operational_week_number,
    )

    start, _end = last_completed_operational_week_cdmx()
    week_numero = last_operational_week_number()

    matches = sorted(RAW_PASOS_PATH.glob("pasos_*.csv"))
    if not matches:
        print(f"No se encontró ningún 'pasos_*.csv' en {RAW_PASOS_PATH}.")
        raise SystemExit(1)

    print(f"Semana operativa: año={start.year} semana={week_numero}")
    print(f"Archivos locales encontrados: {len(matches)}")

    loader = Pasos_load_to_drive(
        file_paths=matches,
        root_folder_id=DRIVE_PASOS_FOLDER_ID,
        year=start.year,
        week_number=week_numero,
    )
    resultado = loader.run()
    print(f"\n  IDs en Drive: {len(resultado)}")