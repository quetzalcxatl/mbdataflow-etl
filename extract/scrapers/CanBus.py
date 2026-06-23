"""Scraping process for the Can Bus report data source."""

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

from ..base import Extractor
from ..helpers.download_helper import get_latest_row_status
from config.settings  import SONDA_QUERY_USER, SONDA_QUERY_PASSWORD
#from ..helpers.CAN_drive_loader import CAN_load_to_drive  


class CanBus_Scraper(Extractor):
    """Download from the Sonda_CanData source."""

    name = "Can_Bus"

    # Constructor sub-method
    # Prepares the download directory 
    def __init__(self, config_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent  # ← un .parent más
        is_serverless = "FUNCTION_TARGET" in os.environ
        if is_serverless:
            self.download_dir = Path("/tmp")
        else:
            self.download_dir = project_root / "data" / "raw" / "downloads_CanBus"
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
    
    # Private sub.method
    # Sinoptico logging process 
    def _login(self, driver: webdriver.Chrome) -> None:

        driver.get("https://cdmx.sinopticoplus.com/#/")
        wait = WebDriverWait(driver, 60)  # Increased timeout, wait to load
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
            username_input.send_keys(SONDA_QUERY_USER) # Credenciales 
            password_input.send_keys(SONDA_QUERY_PASSWORD)
            driver.save_screenshot(str(self.download_dir / "step4_credentials_entered.png"))
            login_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
            driver.save_screenshot(str(self.download_dir / "step5_before_click.png"))
            login_btn.click()
            driver.save_screenshot(str(self.download_dir / "step6_after_click.png"))
        except Exception as e:
            #driver.save_screenshot(str(self.download_dir / "login_error.png"))
            with open(self.download_dir / "login_error_source.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            raise RuntimeError(
                "Login form not found. Check if the site structure has changed or if the page is reachable."
            ) from e
    
    # Navigate to the Can-Data report
    def _navigate_to_report(self, driver: webdriver.Chrome) -> None:
        wait = WebDriverWait(driver, 20)
        # Click the sidebar icon using JavaScript
        sidebar_icon = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "img[src='img/fa-list.png']")))
        driver.execute_script("arguments[0].click();", sidebar_icon)
        driver.save_screenshot(str(self.download_dir / "step7_sidebar_clicked.png"))
        # Wait for the menu item to appear using your XPath
        # Here we are already selecting report instances. 
        # Change to the relevant instances for each report. 
        # XPath to the Can Data button
        menu_item = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="navbar-fixed-left"]/ul/li[9]/ul/li/ul/li[4]/ul/li[4]/a[1]')))
        driver.save_screenshot(str(self.download_dir / "step8_menuitem_visible.png"))
        driver.execute_script("arguments[0].click();", menu_item)
        driver.save_screenshot(str(self.download_dir / "step9_menuitem_clicked.png"))
        # Wait for the report iframe to appear
        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)
        driver.save_screenshot(str(self.download_dir / "step10_CanData_iframe.png"))

        # Un-comment only if _request_report_by_date() is deactivated in scrape() last method:
        #driver.switch_to.default_content()
    
    # Request report verifying actual date
    def _request_report_by_date(self, driver: webdriver.Chrome, date: str) -> None:
        wait = WebDriverWait(driver, 20)
        date_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='filtro.data']"))
        )
        driver.save_screenshot(str(self.download_dir / "step11_datebox_localized.png"))
        date_input.clear()
        date_input.send_keys(date)
        driver.save_screenshot(str(self.download_dir / "step12_date_validated.png"))
        
        wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[ng-click='downloadCsv()']")
            )
        ).click()
        time.sleep(5)
        driver.save_screenshot(str(self.download_dir / "step13_requested.png"))
        driver.switch_to.default_content()
        

    # Query dashboard and download report
    def _navigate_to_downloads(self, driver: webdriver.Chrome) -> None:
        wait = WebDriverWait(driver, 20)
        # Click the sidebar icon using JavaScript
        sidebar_icon = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "img[src='img/fa-list.png']")))
        driver.execute_script("arguments[0].click();", sidebar_icon)
        # Navigate to the download dashboard
        report_dashboard=wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[@id='navbar-fixed-left']/ul/li[9]/ul/li/ul/li[1]/a[1]")))
        driver.execute_script("arguments[0].click();", report_dashboard)

        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)
        driver.save_screenshot(str(self.download_dir / "step14_download_dashboard.png"))

    '''
    In the next method, the download helper (get_latest_row_status) must be used. 
    It acts over the download panel and actively polls until the download link becomes
    available, returning a boolean: True (available) or False (not available). 
    The connector must wait (sleep) until the helper returns True (available) 
    along with the download button, so it can be used within the download method.
    '''
    # Download method
    def _download(self, driver: webdriver.Chrome, date: str)-> Path:
        wait = WebDriverWait(driver, 20)

        def click_query_button():
            """Re-locates and clicks the query button fresh each time to avoid stale references."""
            btn = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[type=submit]")
            ))
            driver.execute_script("arguments[0].click();", btn)

        # --- Trigger the first query to populate the table ---
        click_query_button()
        #time.sleep(5)
        driver.save_screenshot(str(self.download_dir / "step15_request.png"))

        # --- Wait for the table to appear ---
        wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "table#example.table.responsive.tPainelEventos")
        ))
        driver.save_screenshot(str(self.download_dir / "step16_table_visible.png"))
        time.sleep(1)  # Let Angular finish rendering ng-repeat rows

        # --- Poll until the latest row status is COMPLETO ---
        while True:
            result = get_latest_row_status(driver, wait)
            status = result['status']

            if (status == 'COMPLETO'):
                print(f"[STATUS] {status}")
                # --- Find and click the download link on the latest row ---
                #time.sleep(5)
                # Part where we reach the download link button...
                rows = driver.find_elements(By.CSS_SELECTOR, "table.tPainelEventos tbody tr")

                latest_row = rows[result['latest_row_index']]
                latest_date = datetime.strptime(result['latest_date'], "%d/%m/%Y %H:%M:%S")

                existing = set(self.download_dir.glob("*")) # Snapshot antes de la descarga 
                # Click the "Descargar Reporte" link inside the latest row
                download_link = latest_row.find_element(By.CSS_SELECTOR, "a.btn-links")
                driver.execute_script("arguments[0].click();", download_link) # Acciona la descarga
                print(f"[DOWNLOAD] Triggered download for CAN-DATA dated {latest_date.strftime('%d/%m/%Y %H:%M:%S')}")
                driver.save_screenshot(str(self.download_dir / "step15_(a)_download_succesfull.png")) 
                
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

                # --- Size stabilization: wait until file stops growing ---
                previous_size = -1
                while True:
                    current_size = new_file.stat().st_size
                    if current_size == previous_size and current_size > 0:
                        break
                    previous_size = current_size
                    time.sleep(0.5)

                # --- Rename directly, no second file ---
                target = self.download_dir / f"CAN-{date}.csv"
                if target.exists():
                    target.unlink()
                new_file.rename(target)
                return target

            elif status in ('EN PROGRESO', 'ESPERANDO INICIO'):
                print(f"[STATUS] {status}")
                # Report is still being generated — wait and re-trigger the table refresh
                time.sleep(4)
                click_query_button()  # Re-locates the button fresh — avoids stale reference
                # Wait for the table to re-render before checking again
                wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "table#example.table.responsive.tPainelEventos")
                ))
            
            else:
                print(f"[STATUS] {status}")
                # If not COMPLETO and (EN PROGRESO or ESPERANDO INICIO), so the STATUS must be ERROR...
                print("[STATUS = ERROR] Error en la solicitud de reporte")
                break
            
    #---------------------------------------------------------------------------------------------------------

    # Make logout of Sonda platform
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
        

    def scrape(self) -> None:
        """Scrape data from the Sonda PV website and upload the CSV to Google Drive."""
        now = datetime.now()
        date_str  = now.strftime("%d%m%Y")
        date_str_ = now.strftime("%d%m%y")

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            self._request_report_by_date(driver, date_str)
            self._navigate_to_downloads(driver)
            self._download(driver, date_str_)

            time.sleep(1)
            self._logout(driver)
        finally:
            driver.quit()

        return None
    
# Bloque que permite test execution 
# En prompt invocas python -m extract.scrapers.CanBus
if __name__ == "__main__":
    scraper = CanBus_Scraper()
    scraper.run()