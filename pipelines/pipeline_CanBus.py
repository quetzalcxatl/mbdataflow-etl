# pipelines/pipeline_canbus.py
# -*- coding: utf-8 -*-
from datetime import datetime
import pytz

from utils.logger                       import ok, info, err, fmt_fecha, cabecera_fecha
from extract.scrapers.CanBus            import CanBus_Scraper
from transform.transformers.CanBus      import procesar_fecha
from load.loaders.CAN_drive_loader          import CanBus_Drive_Loader


def run():
    tz    = pytz.timezone('America/Mexico_City')
    fecha = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    print("\n" + "═"*55)
    print("  🚌  CANBUS SONDA — Pipeline ETL")
    print(f"  📅  Fecha: {fecha.strftime('%d/%m/%Y')}")
    print("═"*55)

    # ── Extract ───────────────────────────────────────────────
    info("Extract — Descargando archivo del día...")
    try:
        CanBus_Scraper().run()
        ok("Scraper completado")
    except Exception as e:
        err(f"Error en Extract: {e} — pipeline detenido")
        return

    # ── Transform ─────────────────────────────────────────────
    info("Transform — Preprocesando...")
    resultado = procesar_fecha(fecha)
    if resultado is None:
        err("Transform falló o no encontró el archivo — pipeline detenido")
        return
    ok("Transform completado")

    # ── Load ──────────────────────────────────────────────────
    info("Load — Subiendo a Drive...")
    try:
        resumen_carga = CanBus_Drive_Loader().run(fecha=fecha)
        ok("Load completado")
    except Exception as e:
        err(f"Error en Load: {e} — pipeline detenido")
        return

    # ── Resumen ───────────────────────────────────────────────
    print("\n" + "═"*55)
    print("  📋  RESUMEN DEL PIPELINE")
    print(f"  {'Marca':<22} {'Filas':>7}   {'Drive':>6}")
    print(f"  {'─'*22} {'─'*7}   {'─'*6}")
    for nombre, filas, estado in resultado:
        drive = "✅" if resumen_carga.get(nombre) else "❌"
        print(f"  {nombre:<22} {filas:>7}   {drive}")
    print("═"*55 + "\n")


if __name__ == '__main__':
    run()