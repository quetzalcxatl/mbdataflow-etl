"""Connector for the Intervalos report data source."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List

import pandas as pd
import time
from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from config.settings  import SONDA_QUERY_USER, SONDA_QUERY_PASSWORD
from ..base import Extractor


class Interval_Scraper(Extractor):
    """Download and load data for the Interval source."""

    name = "Intervalos"

    # Constructor sub-method
    # Prepares the download directory 
    def __init__(self, config_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent  # ← un .parent más
        is_serverless = "FUNCTION_TARGET" in os.environ
        if is_serverless:
            self.download_dir = Path("/tmp")
        else:
            self.download_dir = project_root / "data" / "raw" / "downloads_Intervalos"
        self.download_dir.mkdir(parents=True, exist_ok=True)  # ← parents=True por si data/raw/ no existe
    
    # Sub-método privado
    # Instanciar Chrome Webdriver mediante Selenium
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
    
    # Sub-método privado
    # Proceso de logeado en la página de Sinoptico
    def _login(self, driver: webdriver.Chrome) -> None:
        driver.get("https://cdmx.sinopticoplus.com/#/")
        wait = WebDriverWait(driver, 60)  # Increased timeout espera a que cargue
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
            username_input.send_keys(SONDA_QUERY_USER) # Credentials
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
        
    # Navegamos al reporte de Viaje
    def _navigate_to_report(self, driver: webdriver.Chrome) -> None:
        wait = WebDriverWait(driver, 30)
        # Click the sidebar icon using JavaScript
        sidebar_icon = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "img[src='img/fa-list.png']")))
        driver.execute_script("arguments[0].click();", sidebar_icon)
        driver.save_screenshot(str(self.download_dir / "step7_sidebar_clicked.png"))
        # Wait for the menu item to appear using your XPath
        # Aquí ya estamos seleccionando instancias de reporte
        # Cambiar por las instancias pertinentes de cada reporte
        # XPATH al boton de Viaje/Viaje
        menu_item = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="navbar-fixed-left"]/ul/li[9]/ul/li/ul/li[3]/ul/li[2]/a[1]')))
        driver.save_screenshot(str(self.download_dir / "step8_menuitem_visible.png"))
        driver.execute_script("arguments[0].click();", menu_item)
        driver.save_screenshot(str(self.download_dir / "step9_menuitem_clicked.png"))
        # Wait for the iframe to appear
        # Esperamos a que se despliegue el cuadro de reporte
        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)
        driver.save_screenshot(str(self.download_dir / "step10_Viaje/Viaje_iframe.png"))

    
    def _request_hourdate_interval(self, driver: webdriver.Chrome, date_i: str, date_f : str,
                                    hour_i: str, hour_f: str) -> Path:
        wait = WebDriverWait(driver, 15)
        i_date_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='filtro.dataInicial']"))
        )
        i_date_input.clear()
        time.sleep(3)
        i_date_input.send_keys(date_i)

        i_hour_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='filtro.horaInicial']"))
        )
        i_hour_input.clear()
        i_hour_input.send_keys(hour_i)
        
        f_date_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='filtro.dataFinal']"))
        )
        f_date_input.clear()
        time.sleep(3)
        f_date_input.send_keys(date_f)

        f_hour_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[ng-model='filtro.horaFinal']"))
        )
        f_hour_input.clear()
        f_hour_input.send_keys(hour_f)
        time.sleep(4)

        # Checkpoint: Implementar el hecho de que debemos clickear dos veces GENERAR REPORTE
        # Esperar a que aparezca la ventana emergente y seleccionar .csv y descargar
        # La descarga no es inmediata. Posterior a esto, debemos dirigirnos a centro de descargas
        # de reportes de viaje y clickear en consultar

        return None
    
    #------------------------------------------------------------------------------------------------------------------------

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

        # Calcular intervalo de semana vencida
        from datetime import date, timedelta
        # Calcular el lunes de la semana actual
        lunes_actual = now - timedelta(days=now.weekday())  # weekday(): lunes=0, domingo=6
        inicio_semana_vencida = lunes_actual - timedelta(days=7)
        fin_semana_vencida = inicio_semana_vencida + timedelta(days=6)

        # Mostrar resultados (opcional)
        print("Fecha actual:", now.strftime("%d/%m/%Y"))
        

        fecha_i = inicio_semana_vencida.strftime("%d%m%Y")
        fecha_f = fin_semana_vencida.strftime("%d%m%Y")
        hora_i = "000000"
        hora_f = "235959"

        print("Inicio de semana vencida:", fecha_i)
        print("Fin de semana vencida:", fecha_f)

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            self._request_hourdate_interval(driver, fecha_i, fecha_f, hora_i, hora_f)
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
# En prompt invocas python -m Extract.scrapers.Intervalos
if __name__ == "__main__":
    scraper = Interval_Scraper()
    scraper.run()
    
