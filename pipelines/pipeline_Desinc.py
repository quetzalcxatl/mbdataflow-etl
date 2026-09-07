"""
Orquestador del pipeline Desincorporaciones y Apoyos.

Flujo EL puro. Una sola sesión de Sonda descarga dos reportes que difieren únicamente
en el campo 'Servicio' del formulario; cada uno aterria en su propia carpeta de Drive
(son carpetas que comparten el mismo directorio raíz, no anidadas).

    scrape (Sonda -> {Desinc: CSV, Apoyo: CSV})
        │
        ├── drive_load(Desinc) -> DRIVE_DESINC_FOLDER_ID
        └── drive_load(Apoyo)  -> DRIVE_APOYO_FOLDER_ID

POLÍTICA DE FALLO: best-effort en las dos estapas, con raise final. Aquí los dos CSV
son indepenientes y válidos exista o no el otro.
"""

from config.settings                              import (DRIVE_DESINC_FOLDER_ID,
                                                          DRIVE_APOYO_FOLDER_ID,)
from utils.logger                                 import ok, info, err
from extract.scrapers.Desincorporaciones          import (Desincorporaciones_Scraper,
                                                          SERVICIOS)
from load.loaders.Desincorporaciones_drive_loader import Desinc_load_to_drive
from utils.dates import yesterday_cdmx


def _validate_env() -> dict[str, str]:
    """
    Verifica la configuración ANTES de abrir Chrome.

    Retorna {prefijo: folder_id}. Las claves deben cubrir todos los prefijos
    de SERVICIOS; el desajuste se detecta aquí, no a mitad de la carga.
    """

    destinos = {
            "Desinc": DRIVE_DESINC_FOLDER_ID,
            "Apoyo": DRIVE_APOYO_FOLDER_ID,
        }
    
    env_por_prefijo = {
        "Desinc": "DRIVE_DESINC_FOLDER_ID",
        "Apoyo": "DRIVE_APOYO_FOLDER_ID",
    }

    faltantes = [env_por_prefijo[p] for p, fid in destinos.items() if not fid]
    if faltantes:
        raise RuntimeError(
            "pipeline_Desinc: configuración incompleta. Faltan env vars: "
            + ", ".join(faltantes)
        )

    # Guarda contra desajuste scraper/pipeline: si alguien agrega un servicio
    # a SERVICIOS sin darle destino, esto falla en el segundo 1.
    sin_destino = [pref for _serv, pref in SERVICIOS if pref not in destinos]
    if sin_destino:
        raise RuntimeError(
            f"pipeline_Desinc: prefijos sin carpeta de Drive configurada: "
            f"{', '.join(sin_destino)}"
        )

    return destinos


def run():
    destinos = _validate_env()
    fecha_datos = yesterday_cdmx()

    print("\n" + "="*55)
    print("==== DESINCORPORACIONES + APOYOS - Pipeline ETL")
    print(f"==== Fecha de datos {fecha_datos.strftime('%d/%m/%Y')}")
    print(f"==== Servicios: {', '.join(p for _s, p in SERVICIOS)}")

    # -------- Extract -----------------------------------------
    info('Extract - Descargando reportes...')
    descargados = Desincorporaciones_Scraper().scrape()
    esperados = [prefijo for _serv, prefijo in SERVICIOS]
    faltantes_extract = [p for p in esperados if p not in descargados]
    ok(f"Extract: {len(descargados)}/{len(esperados)} reportes")

    # ------- Load -------------------------------------------
    # Se sube lo que haya, aunque extract no haya completado todos los ciclos.
    info("Load - Subiendo a Drive...")
    subidos: dict[str, str] = {}
    fallos_load: list[tuple[str, Exception]] = []

    for prefijo in esperados:
        if prefijo not in descargados:
            continue
        try:
            subidos[prefijo] = Desinc_load_to_drive(
                descargados[prefijo], destinos[prefijo]
            ).run()
        except Exception as exc:
            err(f"[{prefijo}] Carga falló: {type(exc).__name__}: {exc}")
            fallos_load.append((prefijo, exc))

    # ------ Resumen --------------------------------------
    print("\n" + "═"*55)
    print("  📋  RESUMEN DEL PIPELINE")
    print(f"  {'─'*45}")
    for prefijo in esperados:
        if prefijo in subidos:
            ok(f"{prefijo:<8} subido. (ID: {subidos[prefijo]})")
        elif prefijo in descargados:
            err(f"{prefijo:<8} descargado pero NO subido.")
        else:
            err(f"{prefijo:<8} no descargado.")
    print("═"*55 + "\n")

    # ── Raise final ───────────────────────────────────────────
    if faltantes_extract or fallos_load:
        detalle = []
        if faltantes_extract:
            detalle.append(f"extract falló en: {', '.join(faltantes_extract)}")
        if fallos_load:
            detalle.append(
                "load falló en: "
                + ", ".join(f"{p} ({type(e).__name__})" for p, e in fallos_load)
            )
        raise RuntimeError("pipeline_Desinc incompleto — " + "; ".join(detalle))


if __name__ == '__main__':
    run()