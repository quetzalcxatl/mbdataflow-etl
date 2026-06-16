# pipelines/pipeline_canbus.py
# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
import pytz

from utils.logger                       import ok, info, err, fmt_fecha, cabecera_fecha
from Extract.scrapers.CanBus            import CanBus_Scraper
from Transform.transformers.CanBus      import procesar_fecha
from Load.loaders.CAN_drive_loader      import CanBus_Drive_Loader


def pedir_fecha(prompt, tz, hoy):
    fmt = "%Y-%m-%d"
    while True:
        try:
            entrada = input(prompt).strip()
            if entrada == '':
                return hoy
            return datetime.strptime(entrada, fmt).replace(tzinfo=tz)
        except ValueError:
            err(f"Formato inválido: '{entrada}'. Usa YYYY-MM-DD (ej: 2026-03-01)")


def pedir_rango():
    tz  = pytz.timezone('America/Mexico_City')
    hoy = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    print("\n" + "═"*55)
    print("  🚌  CANBUS SONDA — Pipeline ETL por rango")
    print("═"*55)
    info(f"Hoy es : {hoy.strftime('%d/%m/%Y')}")
    print("  (Presiona Enter en cualquier campo para usar la fecha de hoy)")
    print("─"*55)

    inicio = pedir_fecha("  📅  Fecha inicio (YYYY-MM-DD): ", tz, hoy)
    while True:
        fin = pedir_fecha("  📅  Fecha fin    (YYYY-MM-DD): ", tz, hoy)
        if fin >= inicio:
            break
        err(f"La fecha fin no puede ser anterior al inicio. Intenta de nuevo.")

    dias = (fin - inicio).days + 1
    ok(f"Rango confirmado: {inicio.strftime('%d/%m/%Y')} → {fin.strftime('%d/%m/%Y')} ({dias} día(s))")
    print("═"*55)
    return inicio, fin


def generar_rango(inicio, fin):
    fechas, actual = [], inicio
    while actual <= fin:
        fechas.append(actual)
        actual += timedelta(days=1)
    return fechas


def run():
    tz            = pytz.timezone('America/Mexico_City')
    hoy           = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    inicio, fin   = pedir_rango()
    fechas        = generar_rango(inicio, fin)

    loader        = CanBus_Drive_Loader()

    info(f"Fechas : {len(fechas)} día(s) a procesar")

    resumen_global, omitidas = {}, []

    for i, fecha in enumerate(fechas, 1):
        cabecera_fecha(fecha, i, len(fechas))
        fecha_str = fmt_fecha(fecha)

        # ── Extract ───────────────────────────────────────────
        # Solo descargamos si es el día de hoy
        if fecha.date() == hoy.date():
            info("Extract — Descargando archivo del día...")
            try:
                CanBus_Scraper().run()
                ok("Scraper completado")
            except Exception as e:
                err(f"Error en Extract: {e} — se omite esta fecha")
                omitidas.append(fecha_str)
                continue
        else:
            info(f"Extract — Fecha histórica, se asume archivo ya descargado en data/raw/")

        # ── Transform ─────────────────────────────────────────
        resultado = procesar_fecha(fecha)
        if resultado is None:
            err("Transform falló o no encontró el archivo — se omite esta fecha")
            omitidas.append(fecha_str)
            continue

        # ── Load ──────────────────────────────────────────────
        try:
            resumen_carga = loader.run(fecha=fecha)
        except Exception as e:
            err(f"Error en Load: {e} — se omite esta fecha")
            omitidas.append(fecha_str)
            continue

        resumen_global[fecha_str] = (resultado, resumen_carga)

    # ── Resumen global ────────────────────────────────────────
    print("\n\n" + "═"*55)
    print("  📋  RESUMEN GLOBAL")
    print("═"*55)

    for fecha_str, (marcas_transform, marcas_load) in resumen_global.items():
        d = fecha_str
        print(f"\n  📅  {d[4:6]}/{d[2:4]}/20{d[:2]}  (CAN-{fecha_str})")
        print(f"  {'Marca':<22} {'Filas':>7}   {'Drive':>6}")
        print(f"  {'─'*22} {'─'*7}   {'─'*6}")
        for nombre, filas, estado in marcas_transform:
            drive = "✅" if marcas_load.get(nombre) else "❌"
            print(f"  {nombre:<22} {filas:>7}   {drive}")

    if omitidas:
        print(f"\n  ⚠️   Fechas omitidas: {', '.join(omitidas)}")

    print("\n" + "═"*55)
    info(f"Procesadas : {len(resumen_global)}/{len(fechas)} fecha(s)")
    print("═"*55 + "\n")


if __name__ == '__main__':
    run()