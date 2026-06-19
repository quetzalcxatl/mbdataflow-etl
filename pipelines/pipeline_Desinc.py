# pipelines/pipeline_desincorporaciones.py
# -*- coding: utf-8 -*-

from utils.logger                               import ok, info, err
from extract.scrapers.Desincorporaciones        import Desincorporaciones_Scraper
from load.loaders.Desincorporaciones_drive_loader     import Desinc_load_to_drive
from utils.dates import yesterday_cdmx

def run():
    fecha_datos = yesterday_cdmx()

    print("\n" + "═"*55)
    print("  📋  DESINCORPORACIONES — Pipeline ETL")
    print(f"  📅  Fecha de datos: {fecha_datos.strftime('%d/%m/%Y')}")

    # ── Extract ───────────────────────────────────────────────
    info("Extract — Descargando archivo del día...")
    try:
        Desincorporaciones_Scraper().run()
        ok("Scraper completado")
    except Exception as e:
        err(f"Error en Extract: {e} — pipeline detenido")
        raise

    # ── Load ──────────────────────────────────────────────────
    info("Load — Subiendo a Drive...")
    loader  = Desinc_load_to_drive()
    file_id = loader.run()

    # ── Resumen ───────────────────────────────────────────────
    print("\n" + "═"*55)
    print("  📋  RESUMEN DEL PIPELINE")
    print(f"  {'─'*45}")
    if file_id:
        ok(f"Desincorporaciones subido. (ID: {file_id})")
    else:
        err("Desincorporaciones no subido.")
        raise RuntimeError("Load step failed: no file_id returned")
    print("═"*55 + "\n")


if __name__ == '__main__':
    run()