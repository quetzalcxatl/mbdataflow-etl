"""Connector for the Sonda_PV data source."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List

import pandas as pd
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from config.settings  import SONDA_QUERY_USER, SONDA_QUERY_PASSWORD
from ..base import Extractor


class SondaPVConnector(Extractor):
    """Download and load data for the Sonda_PV source."""

    name = "Sonda_PV"

    def __init__(self, config_path: Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent.parent
        # Use /tmp for writable storage in Cloud Functions
        is_serverless = "FUNCTION_TARGET" in os.environ
        if is_serverless:
            self.download_dir = Path("/tmp")
        else:
            self.download_dir = base_dir / "downloads"
        self.download_dir.mkdir(exist_ok=True)

    def _start_driver(self) -> webdriver.Chrome:
        options = Options()
        # options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        prefs = {"download.default_directory": str(self.download_dir)}
        options.add_experimental_option("prefs", prefs)
        driver = webdriver.Chrome(options=options)
        driver.set_window_size(1366, 768)  # Standard laptop screen size
        return driver

    def _login(self, driver: webdriver.Chrome) -> None:
        driver.get("https://cdmx.sinopticoplus.com/#/")
        wait = WebDriverWait(driver, 60)  # Increased timeout
        # Always save a screenshot after loading
        driver.save_screenshot(str(self.download_dir / "step1_loaded.png"))
        # Save page source for debugging
        with open(self.download_dir / "step1_source.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        try:
            # Wait for the login form to be present
            username_input = wait.until(EC.presence_of_element_located((By.NAME, "login")))
            driver.save_screenshot(str(self.download_dir / "step2_username_found.png"))
            password_input = wait.until(EC.presence_of_element_located((By.NAME, "password")))
            driver.save_screenshot(str(self.download_dir / "step3_password_found.png"))
            username_input.send_keys(SONDA_QUERY_USER)
            password_input.send_keys(SONDA_QUERY_PASSWORD)
            driver.save_screenshot(str(self.download_dir / "step4_credentials_entered.png"))
            login_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
            driver.save_screenshot(str(self.download_dir / "step5_before_click.png"))
            login_btn.click()
            driver.save_screenshot(str(self.download_dir / "step6_after_click.png"))
        except Exception as e:
            driver.save_screenshot(str(self.download_dir / "login_error.png"))
            with open(self.download_dir / "login_error_source.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            raise RuntimeError(
                "Login form not found. Check if the site structure has changed or if the page is reachable."
            ) from e

    def _navigate_to_report(self, driver: webdriver.Chrome) -> None:
        wait = WebDriverWait(driver, 30)
        # Click the sidebar icon using JavaScript
        sidebar_icon = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "img[src='img/fa-list.png']")))
        driver.execute_script("arguments[0].click();", sidebar_icon)
        driver.save_screenshot(str(self.download_dir / "step7_sidebar_clicked.png"))
        # Wait for the menu item to appear using your XPath
        menu_item = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="navbar-fixed-left"]/ul/li[2]/ul/li/ul/li[13]/a[1]')))
        driver.save_screenshot(str(self.download_dir / "step7_menuitem_visible.png"))
        driver.execute_script("arguments[0].click();", menu_item)
        driver.save_screenshot(str(self.download_dir / "step7_menuitem_clicked.png"))
        # Wait for the iframe to appear
        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)

    def _download_for_hour(self, driver: webdriver.Chrome, hour: str, label: str, date: str) -> Path:
        wait = WebDriverWait(driver, 30)
        time_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='filter.hora']"))
        )
        time_input.clear()
        time_input.send_keys(hour)
        existing = set(self.download_dir.glob("*"))
        wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[ng-click='relatorioJornada()']")
            )
        ).click()
        WebDriverWait(driver, 60).until(
            lambda d: len(set(self.download_dir.glob("*")) - existing) > 0
        )
        new_file = (set(self.download_dir.glob("*")) - existing).pop()
        target = self.download_dir / f"PV_{date}_{label}.csv"
        if target.exists():
            target.unlink()  # Remove existing file before renaming
        new_file.rename(target)
        return target

    def _get_turno(self, now: datetime) -> str:
        """Determine the turno based on the current time."""
        if 4 <= now.hour < 12:
            return "Matutino"
        if 12 <= now.hour <= 23 or now.hour == 0:
            return "Vespertino"
        return "Ninguno"

    def scrape(self) -> pd.DataFrame:
        """Scrape data from the Sonda PV website."""
        now = datetime.now()
        current_hour_str = now.strftime("%H:00:00")
        turno = self._get_turno(now)
        date_str = now.strftime("%Y%m%d")

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            downloaded_file = self._download_for_hour(
                driver, current_hour_str, turno, date_str
            )
        finally:
            driver.quit()

        df = pd.read_csv(downloaded_file, sep=";")
        df.insert(0, "Date", date_str)
        df.insert(1, "Turno", turno)

        df.to_csv(self.download_dir / f"PV_{date_str}_{turno}.csv", index=False)
        return df
    
    def transform(self, transformed_data: pd.DataFrame) -> pd.DataFrame:
        """Transform raw data into the BigQuery schema.

        Currently, this is a passthrough returning the original DataFrame.
        """
        # Convert column types
        transformed_data["Date"] = pd.to_datetime(transformed_data["Date"], format="%Y%m%d")
        transformed_data["Jornada"] = pd.to_numeric(transformed_data["Jornada"], errors="coerce").astype("Int64")

        # Rename columns to BigQuery compatible names
        rename_map = {
            "Date": "date",
            "Turno": "turno",
            "Ruta": "ruta",
            "Jornada": "jornada",
            "Económico": "economico",
            "Empresa Programado": "empresa_programado",
            "Empresa Real": "empresa_real"
        }
        raw_data = transformed_data.rename(columns=rename_map)


        return raw_data

    def load(self, transformed_data: pd.DataFrame) -> None:
        """Load the transformed data into BigQuery."""
        import pandas as pd
        from google.cloud import bigquery
        from google.auth import default
        from mbdataflow import config

        # Use explicit ADC/Workload Identity credentials
        credentials, project = default()
        client = bigquery.Client(credentials=credentials, project=project)
        table_id = f"{config.DATASET_ID}.{config.SONDA_PV_TABLE_ID}"
        job = client.load_table_from_dataframe(
            transformed_data,
            table_id,
            job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
        )
        job.result()  # Wait for the job to complete
        print(f"Uploaded {len(transformed_data)} rows to {table_id}")

        return transformed_data