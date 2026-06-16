from datetime import datetime

def ok(msg):   print(f"  ✅  {msg}")
def info(msg): print(f"  ℹ️   {msg}")
def err(msg):  print(f"  ❌  {msg}")

def seccion(titulo):
    print(f"\n{'─'*55}")
    print(f"  🚌  {titulo}")
    print(f"{'─'*55}")

def paso(n, total, msg):
    print(f"  [{n}/{total}] {msg} ...", end=" ", flush=True)

def fmt_fecha(d):
    """240326  ← formato del nombre de archivo"""
    return f"{d.day:02d}{d.month:02d}{str(d.year)[2:]}"

def cabecera_fecha(fecha, i, total):
    print(f"\n{'═'*55}")
    print(f"  📅  Fecha {i}/{total}:  {fecha.strftime('%d/%m/%Y')}  (CAN-{fmt_fecha(fecha)}.csv)")
    print(f"{'═'*55}")