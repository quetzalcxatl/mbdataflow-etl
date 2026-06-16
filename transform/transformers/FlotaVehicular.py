import pandas as pd
from datetime import datetime
from pathlib import Path

from config.settings import RAW_FLOTAV_PATH, PROCESSED_FLOTAV_PATH
from utils.turno import get_turno
from utils.logger import ok, info, err


def procesar_flota_vehicular(turno: str=None) -> pd.DataFrame | None:
        """
        Lee el .csv de Flota Vehicular del turno actual,
        aplica transformaciones y lo deposita en data/processed/processed_FlotaV/.
        """
        if turno is None:
                turno = get_turno()
        date_str = datetime.now().strftime("%Y%m%d")
        filename = f"PV_{turno}_{date_str}.csv"
        input_path = RAW_FLOTAV_PATH / filename

        # Verificar existencia del archhivo
        info(f"Buscando: {input_path}")
        if not input_path.exists():
                err(f"Archivo no encontrado: {filename}")
                return None
        
        # Leer
        try:
              df = pd.read_csv(input_path)
              ok(f"Archivo cargado: {len(df)} registros")
        except Exception as e:
              err((f"Error al leer '{filename}': {e}"))
              return None
              
        # Transformar
        df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d")
        df["Jornada"] = pd.to_numeric(df["Jornada"], errors="coerce").astype("Int64")

        rename_map = {
        "Date":               "date",
        "Turno":              "turno",
        "Ruta":               "ruta",
        "Jornada":            "jornada",
        "Económico":          "economico",
        "Empresa Programado": "empresa_programado",
        "Empresa Real":       "empresa_real",
        }
        df = df.rename(columns=rename_map)
        ok(f"Transformación completada: {len(df)} filas, {len(df.columns)} columnas")

        #Guardar
        PROCESSED_FLOTAV_PATH.mkdir(parents=True, exist_ok=True)
        output_filename = f"PV_{turno}_{date_str}_processed.csv"
        output_path = PROCESSED_FLOTAV_PATH / output_filename
        df.to_csv(output_path, index=False, encoding='utf-8')
        ok(f"Guardado → {output_filename}")

        return df
                    

# ------------------------------------------------------------------
# Test aislado
# ------------------------------------------------------------------

if __name__ == "__main__":
    resultado = procesar_flota_vehicular()
    if resultado is None:
        print("  ❌  No se pudo procesar el archivo.")
    else:
        print(f"  ✅  Procesamiento completado: {len(resultado)} filas.")