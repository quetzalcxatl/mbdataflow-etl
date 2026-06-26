# pipelines/pipeline_Circuitos.py
# -*- coding: utf-8 -*-

from utils.logger                            import ok, info, err
from utils.dates                             import last_completed_week_cdmx
from extract.scrapers.Circuitos              import Circuitos_Scraper
from load.loaders.Circuitos_drive_loader     import Circuitos_load_to_drive
from config.settings import (
    DRIVE_CIRC_DESGLOSADO_FOLDER_ID,
    DRIVE_CIRC_EJECUTIVO_FOLDER_ID,
)


def run():
    monday, sunday = last_completed_week_cdmx()

    print("\n" + "═"*55)
    print("  📋  CIRCUITOS — Pipeline ETL")
    print(f"  📅  Semana vencida: {monday.strftime('%d/%m/%Y')} → {sunday.strftime('%d/%m/%Y')}")

    # ── Extract ───────────────────────────────────────────────
    info("Extract — Descargando reportes desglosado y ejecutivo...")
    try:
        desglosado_path, ejecutivo_path = Circuitos_Scraper().run()
        ok("Scraper completado")
    except Exception as e:
        err(f"Error en Extract: {e} — pipeline detenido")
        raise

    # ── Load ──────────────────────────────────────────────────
    info("Load — Subiendo reporte desglosado a Drive...")
    desglosado_id = Circuitos_load_to_drive(
        file_path=desglosado_path,
        folder_id=DRIVE_CIRC_DESGLOSADO_FOLDER_ID,
    ).run()

    info("Load — Subiendo reporte ejecutivo a Drive...")
    ejecutivo_id = Circuitos_load_to_drive(
        file_path=ejecutivo_path,
        folder_id=DRIVE_CIRC_EJECUTIVO_FOLDER_ID,
    ).run()

    # ── Resumen ───────────────────────────────────────────────
    print("\n" + "═"*55)
    print("  📋  RESUMEN DEL PIPELINE")
    print(f"  {'─'*45}")
    if desglosado_id:
        ok(f"Circ. desglosado subido. (ID: {desglosado_id})")
    else:
        err("Circ. desglosado no subido.")
    if ejecutivo_id:
        ok(f"Circ. ejecutivo subido.  (ID: {ejecutivo_id})")
    else:
        err("Circ. ejecutivo no subido.")

    if not (desglosado_id and ejecutivo_id):
        raise RuntimeError("Load step failed: at least one file did not upload")

    print("═"*55 + "\n")


if __name__ == '__main__':
    run()