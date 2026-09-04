# pipelines/pipeline_Desinc.py
# -*- coding: utf-8 -*-

from config.settings                              import DRIVE_DESINC_FOLDER_ID
from utils.logger                                 import ok, info
from extract.scrapers.Desincorporaciones          import Desincorporaciones_Scraper
from load.loaders.Desincorporaciones_drive_loader import Desinc_load_to_drive
from utils.dates import yesterday_cdmx


def _validate_env() -> str:
    """
    Verifica la configuración ANTES de abrir Chrome.

    Un env var faltante detectado aquí cuesta un segundo; detectado después
    del scrape cuesta la corrida completa contra Sonda.
    """
    if not DRIVE_DESINC_FOLDER_ID:
        raise RuntimeError(
            "pipeline_Desinc: configuración incompleta. "
            "Falta env var: DRIVE_DESINC_FOLDER_ID"
        )
    return DRIVE_DESINC_FOLDER_ID


def run():
    drive_folder = _validate_env()
    fecha_datos  = yesterday_cdmx()

    print("\n" + "═"*55)
    print("  📋  DESINCORPORACIONES — Pipeline ETL")
    print(f"  📅  Fecha de datos: {fecha_datos.strftime('%d/%m/%Y')}")

    # ── Extract ───────────────────────────────────────────────
    # .scrape() directo, no .run(): alinea con pipeline_Viaje y pipeline_Pasos.
    # Extractor.run() propaga el retorno, pero está anotado '-> None'.
    info("Extract — Descargando reporte de Desincorporaciones...")
    desinc_csv = Desincorporaciones_Scraper().scrape()
    ok(f"Scraper completado: {desinc_csv.name}")

    # ── Load ──────────────────────────────────────────────────
    info("Load — Subiendo a Drive...")
    file_id = Desinc_load_to_drive(desinc_csv, drive_folder).run()

    # ── Resumen ───────────────────────────────────────────────
    print("\n" + "═"*55)
    print("  📋  RESUMEN DEL PIPELINE")
    print(f"  {'─'*45}")
    ok(f"Desincorporaciones subido. (ID: {file_id})")
    print("═"*55 + "\n")


if __name__ == '__main__':
    run()