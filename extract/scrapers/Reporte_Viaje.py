"""Connector for the Viaje report data source."""
from __future__ import annotations

import os
from pathlib import Path

import time
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from ..base import Extractor
from ..helpers.download_helper import get_latest_row_status

from config.settings  import (SONDA_QUERY_USER,
                              SONDA_QUERY_PASSWORD,
                              RAW_VIAJE_PATH,
                              )


class Viaje_Scraper(Extractor):
    """Download and load data for the Reporte_Viaje source."""

    name = "Viaje"

    # Constructor sub-method
    # Prepares the download directory
    def __init__(self, config_path: Path | None = None) -> None:
        self.download_dir = RAW_VIAJE_PATH
        self.download_dir.mkdir(parents=True, exist_ok=True)
    
    # Sub-método privado
    # Instanciar Chrome Webdriver mediante Selenium
    def _start_driver(self) -> webdriver.Chrome:
        options = Options()
        is_cloud_run = any(k in os.environ for k in ("CLOUD_RUN_JOB", "K_SERVICE", "CLOUD_RUN_EXECUTION"))

        # Common prefs: allow multiple automatic downloads
        common_prefs = {
            "profile.default_content_setting_values.automatic_downloads": 1,
            "profile.default_content_settings.popups": 0,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
        }

        if is_cloud_run:
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1366,768")
            options.add_experimental_option("prefs", common_prefs)
            driver = webdriver.Chrome(options=options)
            # Headless Chrome still needs CDP for the download path itself
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(self.download_dir)},
            )
        else:
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            prefs = {**common_prefs, "download.default_directory": str(self.download_dir)}
            options.add_experimental_option("prefs", prefs)
            driver = webdriver.Chrome(options=options)
            driver.set_window_size(1366, 768)

        return driver
    
    
    # Sub-método privado
    # Proceso de logeado en la página de Sinoptico
    def _login(self, driver: webdriver.Chrome) -> None:
        driver.get("https://cdmx.sinopticoplus.com/#/")
        wait = WebDriverWait(driver, 60)  # Increased timeout espera a que cargue
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
        driver.save_screenshot(str(self.download_dir / "step10_Viaje_iframe.png"))

    def _set_ng_date(self, driver: webdriver.Chrome, css_selector: str, d) -> None:
        """Inyecta un Date real al ng-model del datepicker, saltando vista y $parser.
        d: datetime.date. El Date se construye desde partes locales para evitar
        el corrimiento UTC del Chrome headless en Cloud Run."""
        landed = driver.execute_script("""
            const el = document.querySelector(arguments[0]);
            if (!el) return '__NO_INPUT__';
            const y = arguments[1], m = arguments[2], day = arguments[3];
            const dateObj = new Date(y, m - 1, day, 0, 0, 0, 0);   // partes locales, NO ISO string
            const ngEl  = angular.element(el);
            const scope = ngEl.scope();
            const expr  = el.getAttribute('ng-model');             // 'filtro.dataInicial'
            ngEl.injector().get('$parse')(expr).assign(scope, dateObj);
            scope.$apply();
            return el.value;                                       // valor renderizado tras el digest
        """, css_selector, d.year, d.month, d.day)
        expected = d.strftime("%d/%m/%Y")
        if landed != expected:
            raise RuntimeError(f"ng-model no aceptó la fecha: esperaba {expected!r}, quedó {landed!r}")


    def _request_hourdate_interval(self, driver, date_i, date_f, hour_i, hour_f) -> None:
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[ng-model='filtro.dataInicial']")))

        # 1) Fechas PRIMERO (inyección al modelo; send_keys revierte a hoy)
        self._set_ng_date(driver, "input[ng-model='filtro.dataInicial']", date_i)
        self._set_ng_date(driver, "input[ng-model='filtro.dataFinal']",   date_f)

        # 2) Horas AL FINAL (el campo sí acepta texto)
        i_hour = driver.find_element(By.CSS_SELECTOR, "input[ng-model='filtro.horaInicial']")
        i_hour.clear(); i_hour.send_keys(hour_i)
        f_hour = driver.find_element(By.CSS_SELECTOR, "input[ng-model='filtro.horaFinal']")
        f_hour.clear(); f_hour.send_keys(hour_f)
        driver.save_screenshot(str(self.download_dir / "step11_hourdate_interval.png"))

        # Solicitar descarga
        wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button.btn.btn-new.verde-btn.ng-binding[data-target='#container-Central-Arquivo']")
            )
        ).click()
        time.sleep(5)
        driver.save_screenshot(str(self.download_dir / "step12_generate_buttom_clicked.png")) 

        # Pop-up window pidiendo formato de archivo a descargar
        csv_option = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="radioButtons"]/div/label[2]')))
        driver.save_screenshot(str(self.download_dir / "step13_csv_buttom_visible.png"))
        driver.execute_script("arguments[0].click();", csv_option)
        driver.save_screenshot(str(self.download_dir / "step14_csv_buttom_clicked.png"))

        # Click para meter la request
        guardar_buttom = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'button.btn.btn-new.verde-btn.ng-binding[validate-form][data-dismiss="modal"]')))
        driver.save_screenshot(str(self.download_dir / "step13_guardar_buttom_visible.png"))
        driver.execute_script("arguments[0].click();", guardar_buttom)
        driver.save_screenshot(str(self.download_dir / "step14_guardar_buttom_clicked.png"))

        driver.switch_to.default_content()

        return None
    #------------------------------------------------------------------------------------------------------------------------

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

        return None
    
    #------------------------------------------------------------------------------------------------------------------------

    # Download method
    def _download(self, driver: webdriver.Chrome, date_name: str)-> Path:
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
                print(f"[DOWNLOAD] Triggered download for Viaje Report dated {latest_date.strftime('%d/%m/%Y %H:%M:%S')}")
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
                target = self.download_dir / f"RV_{date_name}.csv"
                if target.exists():
                    target.unlink()
                new_file.rename(target)
                return target

            elif status in ('EN PROGRESO', 'ESPERANDO INICIO'):
                print(f"[STATUS] {status}")
                # Report is still being generated — wait and re-trigger the table refresh
                time.sleep(5)
                click_query_button()  # Re-locates the button fresh — avoids stale reference
                # Wait for the table to re-render before checking again
                wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "table#example.table.responsive.tPainelEventos")
                ))
            
            else:
                print(f"[STATUS] {status}")
                # If not COMPLETO and (EN PROGRESO or ESPERANDO INICIO), so the STATUS must be ERROR...
                print("[STATUS = ERROR] Error en la solicitud de reporte")
                raise RuntimeError(f"Reporte de fallo en Sonda con status: {status}!")
    

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
        """Scrape data from the Sonda PV"""
        from utils.dates import last_completed_week_cdmx
        monday, sunday = last_completed_week_cdmx()
        name_date  = f"{monday.strftime('%d%m%y')}_{sunday.strftime('%d%m%y')}"

        hora_i = "000000"
        hora_f = "235959"

        print("Inicio de semana vencida:", monday)
        print("Fin de semana vencida:", sunday)

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            self._request_hourdate_interval(driver, monday, sunday, hora_i, hora_f)
            self._navigate_to_downloads(driver)
            target_path = self._download(driver, name_date)

            time.sleep(1)
            self._logout(driver)
        finally:
            driver.quit()

        return target_path
    
# Bloque que permite test execution 
# En prompt invocas python -m extract.scrapers.Reporte_Viaje
if __name__ == "__main__":
    scraper = Viaje_Scraper()
    scraper.run()
    
