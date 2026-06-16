# -*- coding: utf-8 -*-
import pandas as pd
import re
import os
from pathlib import Path

from config.settings  import RAW_CANBUS_PATH, MARCAS_CONFIG
from utils.logger     import ok, info, err, seccion, paso, fmt_fecha


# ════════════════════════════════════════════════════════════════════════════
# FUNCIONES CORE DE TRANSFORMACIÓN
# ════════════════════════════════════════════════════════════════════════════

def preprocesar_can_data(texto):
    if pd.isna(texto) or str(texto).strip() == '':
        return ''
    texto = re.sub(r'[\n\r\t]+', ' ', texto)
    texto = re.sub(r'\s{2,}', '  ', texto)
    return texto.strip()


def desglosar_columna_simple(texto):
    texto = preprocesar_can_data(texto)
    if texto == '':
        return pd.Series({'Marca': ''})

    resultado = {}
    marcas_conocidas = {'Yutong', 'ADennis', 'Volvo', 'BYD', 'Mercedes', 'Scania'}
    primera_palabra  = texto.split()[0] if texto.split() else ''

    if primera_palabra in marcas_conocidas:
        resultado['Marca'] = primera_palabra
        resto = texto[len(primera_palabra):].strip()
    elif '  ' in texto:
        marca, resto = texto.split('  ', 1)
        resultado['Marca'] = marca.strip()
    else:
        resultado['Marca'] = texto.strip()
        return pd.Series(resultado)

    m = re.search(r'Tanque 1 y 2\s*=\s*([\d.]+)\s+y\s+([\d.]+)\s*(kPa)', resto, re.IGNORECASE)
    if m:
        resultado['Tanque_1_kPa'] = f'{float(m.group(1))} {m.group(3)}'
        resultado['Tanque_2_kPa'] = f'{float(m.group(2))} {m.group(3)}'
        resto = resto[:m.start()] + ' ' + resto[m.end():]

    m = re.search(r'Temperatura Baterias\s*=\s*([\d.]+)\s+(\w+)\s*,\s*([\d.]+)\s+(\w+)', resto, re.IGNORECASE)
    if m:
        resultado['Temp_Bat_Min']      = float(m.group(1))
        resultado['Temp_Bat_Min_Tipo'] = m.group(2)
        resultado['Temp_Bat_Max']      = float(m.group(3))
        resultado['Temp_Bat_Max_Tipo'] = m.group(4)
        resto = resto[:m.start()] + ' ' + resto[m.end():]

    m = re.search(r'Temperatura Baterias\s*=\s*([\d.]+)\s*([A-Za-z°]+)\s*,', resto, re.IGNORECASE)
    if m:
        resultado['Temp_Bat']        = float(m.group(1))
        resultado['Temp_Bat_Unidad'] = m.group(2)
        resto = resto[:m.start()] + ' ' + resto[m.end():]

    m = re.search(
        r'Condicion de Frenos\s*:\s*'
        r'Del_D\s*=\s*([\d.]+)\s*,\s*Del_I\s*=\s*([\d.]+)\s*,\s*'
        r'Tra_D1\s*=\s*([\d.]+)\s*,\s*Tra_I1\s*=\s*([\d.]+)\s*,\s*'
        r'Tra_D2\s*=\s*([\d.]+)\s*,\s*Tra_I2\s*=\s*([\d.]+)',
        resto, re.IGNORECASE)
    if m:
        resultado['Freno_Del_D']  = float(m.group(1))
        resultado['Freno_Del_I']  = float(m.group(2))
        resultado['Freno_Tra_D1'] = float(m.group(3))
        resultado['Freno_Tra_I1'] = float(m.group(4))
        resultado['Freno_Tra_D2'] = float(m.group(5))
        resultado['Freno_Tra_I2'] = float(m.group(6))
        resto = resto[:m.start()] + ' ' + resto[m.end():]

    m = re.search(r'ALARM\s+Motor\s*=\s*(\w+)\s*,\s*Battery\s*=\s*(\w+)\s*,\s*Air\s*=\s*(\w+)', resto, re.IGNORECASE)
    if m:
        resultado['ALARM_Motor']   = m.group(1)
        resultado['ALARM_Battery'] = m.group(2)
        resultado['ALARM_Air']     = m.group(3)
        resto = resto[:m.start()] + ' ' + resto[m.end():]

    m = re.search(r'ALARM\s+Steering\s*=\s*(\w+)\s*,\s*Brake\s*=\s*(\w+)', resto, re.IGNORECASE)
    if m:
        resultado['ALARM_Steering'] = m.group(1)
        resultado['ALARM_Brake']    = m.group(2)
        resto = resto[:m.start()] + ' ' + resto[m.end():]

    m = re.search(r',?\s*Horas\s*=\s*([\d.]+)\s*,?\s*Total Fuel usado\s*=\s*([\d.]+)', resto, re.IGNORECASE)
    if m:
        resultado['Horas']             = float(m.group(1))
        resultado['Total_Fuel_Litros'] = float(m.group(2))
        resto = resto[:m.start()] + ' ' + resto[m.end():]

    partes = re.split(r' {2,}', resto)
    for parte in partes:
        parte = parte.strip().strip(',').strip()
        if not parte or '=' not in parte or not re.search(r'[a-zA-Z]', parte):
            continue
        clave, valor = parte.split('=', 1)
        clave = clave.strip().strip(',').strip()
        valor = valor.strip().strip(',').strip()
        if clave:
            resultado[clave] = valor

    return pd.Series(resultado)


def limpiar_unidades(df):
    df_limpio = df.copy()
    for col in df_limpio.columns:
        if df_limpio[col].dtype == object or pd.api.types.is_string_dtype(df_limpio[col]):
            valores = df_limpio[col].dropna().astype(str)
            patron = r'^([-+]?[0-9]+(?:\.[0-9]+)?)\s*([%°a-zA-Z/]+)$'
            coincidencias = valores.str.extract(patron)
            if coincidencias.notna().all(axis=1).mean() > 0.8:
                unidad = coincidencias[1].mode()[0]
                df_limpio[col] = coincidencias[0].astype(float).round().astype('Int64')
                nuevo_nombre = f"{col.strip()} ({unidad})"
                df_limpio = df_limpio.rename(columns={col: nuevo_nombre})
    return df_limpio


def procesar_marca(df_raw, marca_prefix, columnas_borrar, output_path, pasos_total=5):
    paso(1, pasos_total, "Filtrando registros")
    df = df_raw[df_raw['Can Data'].str.startswith(marca_prefix)].copy()
    df.columns = df.columns.str.replace(r'^\ufeff', '', regex=True)
    print(f"({len(df)} filas)")

    if df.empty:
        print("  ⚠️   Sin datos para esta marca, se omite.")
        return None

    paso(2, pasos_total, "Desglosando columna CAN")
    desglosado = df['Can Data'].apply(desglosar_columna_simple)
    df = pd.concat([df, desglosado], axis=1)
    df = df.drop(columns=['Can Data'])
    print("listo")

    paso(3, pasos_total, "Procesando fechas y horas")
    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df['Hora']  = df['Fecha'].dt.time
    df['Fecha'] = df['Fecha'].dt.normalize()
    cols = df.columns.tolist()
    df = df[['Fecha', 'Hora'] + [c for c in cols if c not in ['Fecha', 'Hora']]]
    print("listo")

    paso(4, pasos_total, "Limpiando unidades")
    df = limpiar_unidades(df)
    print("listo")

    paso(5, pasos_total, f"Guardando → {Path(output_path).name}")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cols_existentes = [c for c in columnas_borrar if c in df.columns]
    df.drop(columns=cols_existentes, inplace=True)
    df.to_csv(output_path, index=False, encoding='utf-8')   # ← .csv en lugar de .xlsx
    print("listo")

    return df


def procesar_fecha(fecha):
    """Lee el CSV de una fecha y corre el pipeline para todas las marcas."""
    fecha_str = fmt_fecha(fecha)
    entrada   = RAW_CANBUS_PATH / f"CAN-{fecha_str}.csv"

    paso(1, 2, f"Leyendo CAN-{fecha_str}.csv")
    if not entrada.exists():
        print()
        info(f"Archivo no encontrado, se omite: {entrada}")
        return None

    try:
        df = pd.read_csv(entrada, sep=';', engine='python', encoding='latin1')
        print(f"({len(df)} filas)")
        ok(f"Archivo cargado: {len(df)} registros")
    except Exception as e:
        print()
        err(f"Error al leer el archivo: {e}")
        return None

    paso(2, 2, "Filtrando registros inválidos (List/Erro)")
    antes = len(df)
    df = df[~df['Can Data'].str.startswith('List')].reset_index(drop=True)
    df = df[~df['Can Data'].str.startswith('Erro')].reset_index(drop=True)
    print(f"(removidos {antes - len(df)} registros)")
    ok(f"Dataset limpio: {len(df)} registros válidos")

    resumen_fecha = []
    for m in MARCAS_CONFIG:
        seccion(m['nombre'])
        output    = m['output_fn'](fecha_str)       # ← solo recibe fecha_str
        resultado = procesar_marca(df, m['prefix'], m['borrar'], output)
        if resultado is not None:
            ok(f"{m['nombre']} guardado  ({len(resultado)} filas, {len(resultado.columns)} cols)")
            resumen_fecha.append((m['nombre'], len(resultado), '✅'))
        else:
            resumen_fecha.append((m['nombre'], 0, '⚠️ '))

    return resumen_fecha

# Bloque que permite test execution 
# En prompt invocas python -m Transform.transformers.canbus_function
if __name__ == '__main__':
    from datetime import datetime
    import pytz

    tz    = pytz.timezone('America/Mexico_City')
    fecha = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)  # ← hoy automático

    print(f"  ℹ️   Procesando fecha: {fecha.strftime('%d/%m/%Y')}")
    resultado = procesar_fecha(fecha)

    if resultado is None:
        print(f"  ❌  No se encontró el archivo para la fecha {fecha.strftime('%d/%m/%Y')}")
    else:
        print(f"  ✅  Procesamiento completado.")
