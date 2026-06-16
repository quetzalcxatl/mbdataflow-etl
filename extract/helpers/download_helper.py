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




    