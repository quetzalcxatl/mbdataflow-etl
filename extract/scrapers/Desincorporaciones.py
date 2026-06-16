"""
Scraping process for the Ocurrencia de Desincorporaciones report data source.
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

from ..base import Extractor
from ..helpers.download_helper import get_latest_row_status
from config.settings  import SONDA_QUERY_USER, SONDA_QUERY_PASSWORD
#from ..helpers.CAN_drive_loader import CAN_load_to_drive  

class Desincorporaciones_Scraper(Extractor):
    """Download from the Sonda_CanData source."""

    name = "Desincorporaciones"

    # Constructor sub-method
    # Prepares the download directory 
    def __init__(self, config_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent  # ← un .parent más
        is_serverless = "FUNCTION_TARGET" in os.environ
        if is_serverless:
            self.download_dir = Path("/tmp")
        else:
            self.download_dir = project_root / "data" / "raw" / "downloads_Desincorporaciones"
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
        #//*[@id="navbar-fixed-left"]/ul/li[3]/ul/li/ul/li/a[1]       Esta semana
        #//*[@id="navbar-fixed-left"]/ul/li[3]/ul/li/ul/li[2]/a[1]    Semana pasada
        menu_item = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="navbar-fixed-left"]/ul/li[3]/ul/li/ul/li/a[1]')))
        driver.save_screenshot(str(self.download_dir / "step8_menuitem_visible.png"))
        driver.execute_script("arguments[0].click();", menu_item)
        driver.save_screenshot(str(self.download_dir / "step9_menuitem_clicked.png"))
        # Wait for the report iframe to appear
        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)
        driver.save_screenshot(str(self.download_dir / "step10_Desincorporaciones_iframe.png"))

        # Un-comment only if _request_report_by_date() is deactivated in scrape() last method:
        #driver.switch_to.default_content()


    # Request report verifying actual date
    def _download_report_by_date_and_hour(
            self, driver: webdriver.Chrome, i_date: str, f_date: str, i_hour: str, f_hour: str, name_date: str) -> None:
        wait = WebDriverWait(driver, 20)

        i_date_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='dateStart']"))
        )
        driver.save_screenshot(str(self.download_dir / "step11_initial_datebox_localized.png"))
        i_date_input.clear()
        i_date_input.send_keys(i_date)
        driver.save_screenshot(str(self.download_dir / "step12_inital_date_validated.png"))

        i_hour_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='faixaHoraInicial']"))
        )
        driver.save_screenshot(str(self.download_dir / "step13_initial_hourbox_localized.png"))
        i_hour_input.clear()
        i_hour_input.send_keys(i_hour)
        driver.save_screenshot(str(self.download_dir / "step14_inital_hour_validated.png"))

        f_date_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='dateEnd']"))
        )
        driver.save_screenshot(str(self.download_dir / "step15_final_datebox_localized.png"))
        f_date_input.clear()
        f_date_input.send_keys(f_date)
        driver.save_screenshot(str(self.download_dir / "step16_final_date_validated.png"))

        f_hour_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='faixaHoraFinal']"))
        )
        driver.save_screenshot(str(self.download_dir / "step17_final_hourbox_localized.png"))
        f_hour_input.clear()
        f_hour_input.send_keys(f_hour)
        driver.save_screenshot(str(self.download_dir / "step18_final_hour_validated.png"))

        wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[ng-click='consultar']")
            )
        ).click()
        time.sleep(1)
        driver.save_screenshot(str(self.download_dir / "step19_requested.png"))
        #driver.switch_to.default_content()

        existing = set(self.download_dir.glob("*")) # Snapshot antes de la descarga
        #Lo encuentra pero no acciona ninguna descarga.....
        action_download = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "span[ng-csv = 'gerarCsvDesincorporacion()']")))
        driver.execute_script("arguments[0].click();", action_download)
        driver.save_screenshot(str(self.download_dir / "step20_actionaded_download.png"))
        #time.sleep(5)
        
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
        target = self.download_dir / f"Desinc_{name_date}.csv"
        if target.exists():
            target.unlink()
        new_file.rename(target)
        return target
        


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

#---------------------------------Scrape_Method------------------------------------------
    def scrape(self) -> None:
        """Scrape data from the Sonda PV website and upload the CSV to Google Drive."""
        now = datetime.now()
        date_str  = now.strftime("%d%m%Y")
        date_str_ = now.strftime("%d%m%y")

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            self._download_report_by_date_and_hour(driver, date_str, date_str, '000000', '235959', date_str_)
            #self._navigate_to_downloads(driver)
            #self._download(driver, date_str_)

            time.sleep(1)
            self._logout(driver)
        finally:
            driver.quit()
        
        '''loader = CAN_load_to_drive(
            download_dir=self.download_dir,
            parent_folder_id=self.parent_folder_id,
        )
        file_id = loader.run()'''

        return None    

# Bloque que permite test execution 
# En prompt invocas python -m Extract.scrapers.Desincorporaciones
if __name__ == "__main__":
    scraper = Desincorporaciones_Scraper()
    scraper.run()