# pipelines/pipeline_desincorporaciones.py
# -*- coding: utf-8 -*-
from datetime import datetime
import pytz

from utils.logger                               import ok, info, err
from extract.scrapers.Desincorporaciones        import Desincorporaciones_Scraper
from load.loaders.Desincorporaciones_drive_loader     import Desinc_load_to_drive


def run():
    tz    = pytz.timezone('America/Mexico_City')
    fecha = datetime.now(tz)

    print("\n" + "═"*55)
    print("  📋  DESINCORPORACIONES — Pipeline ETL")
    print(f"  📅  Fecha: {fecha.strftime('%d/%m/%Y')}")
    print("═"*55)

    # ── Extract ───────────────────────────────────────────────
    info("Extract — Descargando archivo del día...")
    try:
        Desincorporaciones_Scraper().run()
        ok("Scraper completado")
    except Exception as e:
        err(f"Error en Extract: {e} — pipeline detenido")
        return

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
    print("═"*55 + "\n")


if __name__ == '__main__':
    run()