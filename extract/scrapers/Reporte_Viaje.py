"""Connector for the Viaje report data source."""
from __future__ import annotations

import os
from pathlib import Path

import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from ..base import Extractor

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
        """Scrape data from the Sonda PV"""
        from utils.dates import last_completed_week_cdmx
        monday, sunday = last_completed_week_cdmx()

        hora_i = "000000"
        hora_f = "235959"

        print("Inicio de semana vencida:", monday)
        print("Fin de semana vencida:", sunday)

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            self._request_hourdate_interval(driver, monday, sunday, hora_i, hora_f)
            #self._navigate_to_downloads(driver)
            #self._download(driver, date_str_)

            time.sleep(1)
            self._logout(driver)
        finally:
            driver.quit()
        

        return None
    
# Bloque que permite test execution 
# En prompt invocas python -m extract.scrapers.Reporte_Viaje
if __name__ == "__main__":
    scraper = Viaje_Scraper()
    scraper.run()
    
