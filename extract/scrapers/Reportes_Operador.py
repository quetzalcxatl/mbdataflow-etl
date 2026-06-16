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

class ReporteOperadores_Scraper(Extractor):
    """Download from the Sonda_CanData source."""

    name = "Reporte_Operadores"

    # Constructor sub-method
    # Prepares the download directory 
    def __init__(self, config_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent  # ← un .parent más
        is_serverless = "FUNCTION_TARGET" in os.environ
        if is_serverless:
            self.download_dir = Path("/tmp")
        else:
            self.download_dir = project_root / "data" / "raw" / "downloads_Reporte_Operadores"
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
        
    # Navigate to the Reportes Operador report
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
        menu_item = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="navbar-fixed-left"]/ul/li[8]/ul/li/a[1]')))
        driver.save_screenshot(str(self.download_dir / "step8_menuitem_visible.png"))
        driver.execute_script("arguments[0].click();", menu_item)
        driver.save_screenshot(str(self.download_dir / "step9_menuitem_clicked.png"))
        # Wait for the report iframe to appear
        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)
        driver.save_screenshot(str(self.download_dir / "step10_RepOperadores_iframe.png"))

        # Un-comment only if _request_report_by_date() is deactivated in scrape() last method:
        #driver.switch_to.default_content()


    def _download_hourdate_interval(self, driver: webdriver.Chrome, fecha_name: str, date_i: str, date_f : str,
                                    hour_i: str, hour_f: str) -> None:
        wait = WebDriverWait(driver, 15)

        i_date_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='data']"))
        )
        i_date_input.clear()
        i_date_input.send_keys(date_i)

        i_hour_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='hora']"))
        )
        i_hour_input.clear()
        i_hour_input.send_keys(hour_i)
        
        f_date_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='dataFim']"))
        )
        f_date_input.clear()
        f_date_input.send_keys(date_f)

        f_hour_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='horaFim']"))
        )
        f_hour_input.clear()
        f_hour_input.send_keys(hour_f)

        Fecha_Registro = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="radioButtons"]/div/label[2]')))
        driver.save_screenshot(str(self.download_dir / "step11_Fecha_Registro_selected.png"))
        driver.execute_script("arguments[0].click();", Fecha_Registro)


        existing = set(self.download_dir.glob("*")) # Snapshot antes de la descarga
        #Lo encuentra pero no acciona ninguna descarga.....
        download_csv = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "span[filename='ReportesOperador']")))
        driver.execute_script("arguments[0].click();", download_csv)
        driver.save_screenshot(str(self.download_dir / "step12_Fecha_download_actionaded.png"))
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
        target = self.download_dir / f"Reportes_Operador_{fecha_name}.csv"
        if target.exists():
            target.unlink()
        new_file.rename(target)
        return target
    
    '''def _download(self, driver: webdriver.Chrome) -> None:
        wait = WebDriverWait(driver, 15)'''


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
        from datetime import datetime, timedelta

        now = datetime.now()
        # Calcular el último viernes (incluyendo hoy si es viernes)
        # weekday(): lunes=0, martes=1, miércoles=2, jueves=3, viernes=4, sábado=5, domingo=6
        days_since_friday = (now.weekday() - 4) % 7   # 0 si hoy es viernes
        last_friday = now - timedelta(days=days_since_friday)

        # La semana vencida comienza en el viernes anterior al último viernes
        inicio_semana_vencida = last_friday - timedelta(days=7)
        fin_semana_vencida = inicio_semana_vencida + timedelta(days=6)  # jueves

        # Formatear
        fecha_i = inicio_semana_vencida.strftime("%d%m%Y")
        fecha_f = fin_semana_vencida.strftime("%d%m%Y")
        fecha_i_ = inicio_semana_vencida.strftime("%d%m%y")
        fecha_f_ = fin_semana_vencida.strftime("%d%m%y")
        hora_i = "000000"
        hora_f = "235959"
        fecha_name = f"{fecha_i_}_a_{fecha_f_}"

        print("Fecha actual:", now.strftime("%d/%m/%Y"))
        print("Inicio de semana vencida (viernes):", inicio_semana_vencida.strftime("%d/%m/%Y"))
        print("Fin de semana vencida (jueves):", fin_semana_vencida.strftime("%d/%m/%Y"))

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            self._download_hourdate_interval(driver, fecha_name, fecha_i, fecha_f, hora_i, hora_f)
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
# En prompt invocas python -m Extract.scrapers.Reporte_Operadores
if __name__ == "__main__":
    scraper = ReporteOperadores_Scraper()
    scraper.run()    
    