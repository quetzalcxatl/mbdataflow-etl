from __future__ import annotations

'''
Ayuda en la lógica de descarga dentro de la plataforma de sonda
'''

#import json
#import os
from datetime import datetime
import time
#from pathlib import Path
#from typing import List

#import pandas as pd
from selenium import webdriver
from selenium.common import StaleElementReferenceException
#from selenium.webdriver import ActionChains
#from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

#from .base import Connector


# Devuelve el estatus de la solicitud de descarga más reciente
def latest_row_status(driver: webdriver.Chrome) -> dict:
        
    """
    Finds the row with the latest date in 'Fecha/Hora Solicitación'
    and returns both the date and its corresponding status.
    """
    rows = driver.find_elements(By.CSS_SELECTOR, "table.tPainelEventos tbody tr")

    latest_date = None
    latest_status = None

    for row in rows:
        cells = row.find_elements(By.TAG_NAME, "td")
        if not cells:
            continue

        # --- Parse the date from the first column ---
        try:
            date_str = cells[0].text.strip()  # e.g. "12/03/2026 11:01:26"
            row_date = datetime.strptime(date_str, "%d/%m/%Y %H:%M:%S")
        except ValueError:
            continue  # Skip rows with unparseable dates

        # --- Extract status text (lives inside a <span> in the 4th cell) ---
        try:
            status_span = cells[3].find_element(By.TAG_NAME, "span")
            row_status = status_span.text.strip()
        except Exception:
            row_status = cells[3].text.strip()  # Fallback to raw cell text

        # --- Keep track of the most recent row ---
        if latest_date is None or row_date > latest_date:
            latest_date = row_date
            latest_status = row_status

    return {
        "latest_date": latest_date.strftime("%d/%m/%Y %H:%M:%S") if latest_date else None,
        "status": latest_status
    }


def get_latest_row_status(driver, wait: WebDriverWait) -> dict:
    """
    Always re-fetches the table from the DOM to avoid StaleElementReferenceException.
    Returns a dict with 'status' and 'latest_row_index' (its position in the table).
    """
    # Wait for the table to be present and stable before touching it
    wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "table#example.table.responsive.tPainelEventos tbody tr")
    ))

    # Small buffer to let Angular finish re-rendering the ng-repeat rows
    time.sleep(1)

    # Re-query everything fresh — never reuse old element references after a DOM update
    rows = driver.find_elements(By.CSS_SELECTOR, "table.tPainelEventos tbody tr")

    latest_date = None
    latest_status = None
    latest_row_index = None

    for index, row in enumerate(rows):
        try:
            # Re-fetch cells fresh on every iteration
            cells = row.find_elements(By.TAG_NAME, "td")
            if not cells:
                continue

            date_str = cells[0].text.strip()
            row_date = datetime.strptime(date_str, "%d/%m/%Y %H:%M:%S")

        except (ValueError, StaleElementReferenceException):
            # Row disappeared mid-loop — skip it and continue
            continue

        try:
            status_span = cells[3].find_element(By.TAG_NAME, "span")
            row_status = status_span.text.strip()
        except StaleElementReferenceException:
            continue
        except Exception:
            row_status = cells[3].text.strip()

        if latest_date is None or row_date > latest_date:
            latest_date = row_date
            latest_status = row_status
            latest_row_index = index

    return {
        "latest_date": latest_date.strftime("%d/%m/%Y %H:%M:%S") if latest_date else None,
        "status": latest_status,
        "latest_row_index": latest_row_index
    }


# --------------------------------------------------------------------------
# Identificación de filas por clave (fecha, nombre)
# --------------------------------------------------------------------------
# "La fila más reciente" no identifica NUESTRA solicitud: si la recién
# encolada aún no se renderiza, la más reciente es la anterior (ya COMPLETO)
# y se descarga el reporte equivocado. Estas funciones permiten comparar la
# tabla contra una foto previa y localizar una fila por su clave.

CENTRAL_ROWS_CSS = "table.tPainelEventos tbody tr"
# Encabezados: th o td dentro de thead; si no hay thead, los th de cualquier fila.
CENTRAL_HEADERS_CSS = ("table.tPainelEventos thead th, "
                       "table.tPainelEventos thead td")
CENTRAL_HEADERS_FALLBACK_CSS = "table.tPainelEventos tr th"


def _header_texts(driver) -> list[str]:
    """Texto de los encabezados vía textContent: .text devuelve '' si el
    elemento está oculto por CSS (tablas responsive suelen ocultar el thead)."""
    elems = (driver.find_elements(By.CSS_SELECTOR, CENTRAL_HEADERS_CSS)
             or driver.find_elements(By.CSS_SELECTOR, CENTRAL_HEADERS_FALLBACK_CSS))
    return [" ".join((e.get_attribute("textContent") or "").split()).lower()
            for e in elems]


def _central_column_indexes(driver) -> dict:
    """Resuelve las columnas fecha/nombre/status por el texto del encabezado.

    Fallback a las posiciones históricas (fecha=0, status=3) para fecha y
    status; 'nombre' no tiene fallback: si no aparece, falla en voz alta y
    vuelca el HTML de la tabla para diagnosticar.
    """
    headers = _header_texts(driver)

    def _find(pred):
        return next((i for i, h in enumerate(headers) if pred(h)), None)

    fecha = _find(lambda h: "fecha" in h and "solicit" in h)
    if fecha is None:
        fecha = _find(lambda h: "fecha" in h)
    nombre = _find(lambda h: "nombre" in h)
    status = _find(lambda h: "status" in h or "estatus" in h)

    if nombre is None:
        tables = driver.find_elements(By.CSS_SELECTOR, "table.tPainelEventos")
        snippet = (tables[0].get_attribute("outerHTML") or "")[:3000] if tables else "<sin tabla>"
        raise RuntimeError(
            f"La tabla de la Central no tiene columna 'Nombre'. "
            f"Encabezados: {headers}\nHTML de la tabla (recortado):\n{snippet}")
    return {
        "fecha": 0 if fecha is None else fecha,
        "nombre": nombre,
        "status": 3 if status is None else status,
    }


def _cell_text(cell) -> str:
    """Texto de la celda; el status vive en un <span> interno."""
    spans = cell.find_elements(By.TAG_NAME, "span")
    text = spans[0].text.strip() if spans else ""
    return text or cell.text.strip()


def read_central_rows(driver) -> list[dict]:
    """Lee todas las filas de la Central como dicts {fecha, nombre, status}.

    Filas con fecha no parseable o que quedan stale a mitad de la lectura se
    omiten. 'fecha' se devuelve tal cual aparece (dd/mm/yyyy HH:MM:SS).

    Sin filas (Central vacía o tabla no desplegada) -> [] sin exigir encabezados.
    """
    rows = driver.find_elements(By.CSS_SELECTOR, CENTRAL_ROWS_CSS)
    if not rows:
        return []
    cols = _central_column_indexes(driver)
    needed = max(cols.values())
    out: list[dict] = []
    for row in rows:
        try:
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) <= needed:
                continue
            fecha = cells[cols["fecha"]].text.strip()
            datetime.strptime(fecha, "%d/%m/%Y %H:%M:%S")  # valida formato
            out.append({
                "fecha": fecha,
                "nombre": cells[cols["nombre"]].text.strip(),
                "status": _cell_text(cells[cols["status"]]),
            })
        except (ValueError, StaleElementReferenceException):
            continue
    return out


def find_row_element(driver, fecha: str, nombre: str):
    """Re-localiza el <tr> cuya clave es (fecha, nombre). Lanza si no está o
    si hay más de uno (la clave dejaría de ser inequívoca)."""
    cols = _central_column_indexes(driver)
    needed = max(cols["fecha"], cols["nombre"])
    matches = []
    for row in driver.find_elements(By.CSS_SELECTOR, CENTRAL_ROWS_CSS):
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) <= needed:
            continue
        if (cells[cols["fecha"]].text.strip() == fecha
                and cells[cols["nombre"]].text.strip() == nombre):
            matches.append(row)
    if len(matches) != 1:
        raise RuntimeError(
            f"Se esperaba 1 fila ({fecha!r}, {nombre!r}) en la Central, "
            f"hay {len(matches)}")
    return matches[0]




    