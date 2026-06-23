"""
Scraping process for the Flota Vehicular report data source
"""

from __future__ import annotations

import json
import os
from datetime import datetime
import time
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


class FlotaV_Scraper(Extractor):
    """Download and load data for the Sonda_PV source."""

    name = "Flota_Vehicular"

    # Constructor sub-method
    # Prepares the download directory
    def __init__(self, config_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent  # ← un .parent más
        is_serverless = "FUNCTION_TARGET" in os.environ
        if is_serverless:
            self.download_dir = Path("/tmp")
        else:
            self.download_dir = project_root / "data" / "raw" / "downloads_Flota_Vehicular"
        self.download_dir.mkdir(parents=True, exist_ok=True)  # ← parents=True por si data/raw/ no existe


    # Private sub-method
    # Instanciate Chrome Webdriver throught Selenium package
    def _start_driver(self) -> webdriver.Chrome:
        options = Options()
        # options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        prefs = {"download.default_directory": str(self.download_dir)}
        options.add_experimental_option("prefs", prefs) # Add our default directory 
        driver = webdriver.Chrome(options=options)
        driver.set_window_size(1366, 768)  # Standard laptop screen size
        return driver

    # Private su-method
    # Sinoptico logging process
    def _login(self, driver: webdriver.Chrome) -> None:
        driver.get("https://cdmx.sinopticoplus.com/#/")
        wait = WebDriverWait(driver, 60)  # Increased timeout, wait to load 
        # Always save a screenshot after loading
        #driver.save_screenshot(str(self.download_dir / "step1_loaded.png"))
        # Save page source for debugging
        with open(self.download_dir / "step1_source.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        try:
            # Wait for the login form to be present
            username_input = wait.until(EC.presence_of_element_located((By.NAME, "login")))
            #driver.save_screenshot(str(self.download_dir / "step2_username_found.png"))
            password_input = wait.until(EC.presence_of_element_located((By.NAME, "password")))
            #driver.save_screenshot(str(self.download_dir / "step3_password_found.png"))
            username_input.send_keys(SONDA_QUERY_USER) # Credentials
            password_input.send_keys(SONDA_QUERY_PASSWORD)
            #driver.save_screenshot(str(self.download_dir / "step4_credentials_entered.png"))
            login_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
            #driver.save_screenshot(str(self.download_dir / "step5_before_click.png"))
            login_btn.click()
            #driver.save_screenshot(str(self.download_dir / "step6_after_click.png"))
        except Exception as e:
            #driver.save_screenshot(str(self.download_dir / "login_error.png"))
            with open(self.download_dir / "login_error_source.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            raise RuntimeError(
                "Login form not found. Check if the site structure has changed or if the page is reachable."
            ) from e
    # Navigate to the Flota Vehicular report 
    def _navigate_to_report(self, driver: webdriver.Chrome) -> None:
        wait = WebDriverWait(driver, 30)
        # Click the sidebar icon using JavaScript
        sidebar_icon = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "img[src='img/fa-list.png']")))
        driver.execute_script("arguments[0].click();", sidebar_icon)
        driver.save_screenshot(str(self.download_dir / "step7_sidebar_clicked.png"))
        # Wait for the menu item to appear using your XPath
        # Here we are already selecting report instances.
        # Change to the relevant instances for each report
        # XPath to the Flota Vehicular button
        menu_item = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="navbar-fixed-left"]/ul/li[2]/ul/li/ul/li[13]/a[1]')))
        driver.save_screenshot(str(self.download_dir / "step8_menuitem_visible.png"))
        driver.execute_script("arguments[0].click();", menu_item)
        driver.save_screenshot(str(self.download_dir / "step9_menuitem_clicked.png"))
        # Wait for the iframe to appear
        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)
        driver.save_screenshot(str(self.download_dir / "step_FV_panel.png"))

    def _download_for_hour(self, driver: webdriver.Chrome, hour: str, label: str, date: str) -> Path:
        wait = WebDriverWait(driver, 30)
        time_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='filter.hora']"))
        )
        time_input.clear()
        time_input.send_keys(hour)
        existing = set(self.download_dir.glob("*")) # Snapshot antes de la descarga 
        wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[ng-click='relatorioJornada()']") # Acciona descarga
            )
        ).click()

        # --- Poll until a real .csv appears (no partials) ---
        timeout = 120
        interval = 1
        elapsed = 0

        while elapsed < timeout:
            current_files = set(self.download_dir.glob("*"))
            new_files = current_files - existing

            has_partial = any(f.suffix in ('.crdownload', '.tmp') for f in new_files)
            real_csvs = [f for f in new_files if f.suffix == '.csv']

            if real_csvs and not has_partial:
                new_file = real_csvs[0]
                break

            time.sleep(interval)
            elapsed += interval
        else:
            raise TimeoutError(f"Download did not complete within {timeout} seconds.")
        
        # --- Size stabilization wait untill file stops growing ---
        previous_size = -1
        while True:
            current_size = new_file.stat().st_size
            if current_size == previous_size and current_size > 0:
                break
            previous_size = current_size
            time.sleep(0.5)

        # Espera a que aparezca el archivo en el directorio de descarga
        WebDriverWait(driver, 60).until(
            lambda d: len(set(self.download_dir.glob("*")) - existing) > 0 
        )
        new_file = (set(self.download_dir.glob("*")) - existing).pop() 
        target = self.download_dir / f"PV_{label}_{date}.csv"
        if target.exists():
            target.unlink()  # Remove existing file before renaming
        new_file.rename(target)
        return target

    
    # Hacemos logout de Sonda platform
    def _logout(self, driver: webdriver.Chrome) -> None:
        wait = WebDriverWait(driver, 20)
        driver.switch_to.default_content()

        sidebar_icon = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "img[src='img/fa-list.png']")))
        driver.execute_script("arguments[0].click();", sidebar_icon)
        driver.save_screenshot(str(self.download_dir / "logout1_sidebar_clicked.png"))
        
        logout_icon = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[ng-click='logout()']")))
        driver.execute_script("arguments[0].click();", logout_icon)
        driver.save_screenshot(str(self.download_dir / "logout2_logout_clicked.png"))

        logout_confirm = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[class='confirm confirm-btn']")))
        logout_confirm.click()
        driver.save_screenshot(str(self.download_dir / "logout3_logout_confirmed.png"))
        
        #driver.quit()

    def scrape(self) -> pd.DataFrame:
        """Scrape data from the Sonda PV website."""
        from utils.turno import get_turno
        now = datetime.now()
        current_hour_str = now.strftime("%H:00:00")
        turno = get_turno(now)
        date_str = now.strftime("%Y%m%d")
        #date_str = now.strftime("%d%m%y")

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            downloaded_file = self._download_for_hour(
                driver, current_hour_str, turno, date_str
            )
            #time.sleep(5)
            self._logout(driver)
        finally:
            driver.quit()

        # Leer, enriquecer y sobrescribir el mismo archivo
        df = pd.read_csv(downloaded_file, sep=";")
        df.insert(0, "Date", date_str)
        df.insert(1, "Turno", turno)
        df.to_csv(downloaded_file, index=False)  # ← sobrescribe el mismo archivo

        return df

# Test execution block 
# Terminal call: python -m extract.scrapers.FlotaVehicular
if __name__ == "__main__":
    scraper = FlotaV_Scraper()
    scraper.run() 