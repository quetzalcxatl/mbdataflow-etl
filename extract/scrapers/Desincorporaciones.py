"""
Scraping process for the Ocurrencia de Desincorporaciones report data source.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from utils.dates import yesterday_cdmx
from ..base import Extractor
from config.settings import SONDA_QUERY_USER, SONDA_QUERY_PASSWORD, RAW_DESINC_PATH

# --- Constates de módulo -----------------------------------------------
SERVICIO_DESINCORPORACION = "Desincorporacion"
SERVICIO_APOYO = "Apoyo"

SERVICIO_CONTAINER_CSS = "#inputCategoria"

SERVICIO_TRIGGER_CSS = "#inputCategoria a.select2-choice"

SERVICIO_MATCH_CSS = "#inputCategoria a.ui-select-match span.select2-chosen[ng-transclude]"

SERVICIO_OPEN_CSS = "#inputCategoria.open"

class Desincorporaciones_Scraper(Extractor):
    """Download from the Sonda_CanData source."""

    name = "Desincorporaciones"

    # Constructor sub-method
    # Prepares the download directory 
    def __init__(self, config_path: Path | None = None) -> None:
        self.download_dir = RAW_DESINC_PATH
        self.download_dir.mkdir(parents=True, exist_ok=True)

    # Private sub-method
    # Instanciate Chrome Webdriver throught Selenium package
    def _start_driver(self) -> webdriver.Chrome:
        options = Options()
        is_cloud_run = any(k in os.environ for k in ("CLOUD_RUN_JOB", "K_SERVICE", "CLOUD_RUN_EXECUTION"))

        if is_cloud_run:
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1366,768")
            driver = webdriver.Chrome(options=options)
            # Headless Chrome ignores download prefs — must use CDP
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(self.download_dir)},
            )
        else:
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            prefs = {"download.default_directory": str(self.download_dir)}
            options.add_experimental_option("prefs", prefs)
            driver = webdriver.Chrome(options=options)
            driver.set_window_size(1366, 768)

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

    def _select_servicio(self, driver:webdriver.Chrome, servicio: str, timeout: int = 20) -> None:
        """
        Selecciona un valor del ui-select 'Servicio'. El campo pasó de tener un valor a tener dos,
        ('Desincorporacion', 'Apoyo'). El orden abrir -> localizar es obligatorio.
        """
        wait = WebDriverWait(driver, timeout)
        trigger = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, SERVICIO_TRIGGER_CSS)))
        driver.execute_script("arguments[0].click();", trigger)

        # El contenedor alcanza la clase 'open' en el mismo digest que puebla
        # gating evita buscar opciones del DOM apun vacío
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, SERVICIO_OPEN_CSS)))

        # normalize-space(.) sobre el <div>, no text(): ng-bind-html pasa por
        # el filtro `highlight`, que con search vacío rinde texto plano pero
        # que si algún día se habilita el buscador partiría el nodo de texto
        # inyectando <span class="select2-match">.
        option_xpath = (
            "//*[@id='inputCategoria']"
            "//li[contains(@class, 'ui-select-choices-row')]"
            f"[.//div[normalize-space(.)='{servicio}']]"
        )
        option = wait.until(EC.element_to_be_clickable((By.XPATH, option_xpath)))
        driver.execute_script("arguments[0].click()", option)

        # Post-condición. Sin ella, un click que no dispara el digest de
        # Angular pasa inadvertido y el CSV baja con el servicio equivocado,
        # sin error y con el nombre correcto. El .strip() es necesario: el
        # span trae salto de línea e indentación del template.
        wait.until(lambda d: d.find_element(
            By.CSS_SELECTOR, SERVICIO_MATCH_CSS).text.strip() == servicio)
        print(f"[SERVICIO] {servicio}")
        

    # -- Etapas del ciclo de reporte ---------------------------------------------------------------
    
    def _set_date_hour_interval(self, driver: webdriver.Chrome, 
                                i_date: str, f_date: str,
                                i_hour: str, f_hour: str) -> None:
        """Llena los cuatro campos del intervalo fecha/hora"""
        wait = WebDriverWait(driver, 20)

        for css, valor in (
            ("input[ng-model='dateStart']", i_date),
            ("input[ng-model='faixaHoraInicial']", i_hour),
            ("input[ng-model='dateEnd']", f_date),
            ("input[ng-model='faixaHoraFinal']", f_hour),
        ):
            campo = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, css)))
            driver.save_screenshot(str(self.download_dir / f"step_{css}_located.png"))
            campo.clear()
            campo.send_keys(valor)
            driver.save_screenshot(str(self.download_dir / f"step_{css}_filled.png"))

    def _consultar(self, driver: webdriver.Chrome) -> None:
        """Dispara la consulta con los filtros ya puestos"""
        wait = WebDriverWait(driver, 20)
        wait.until(EC.element_to_be_clickable(
                   (By.CSS_SELECTOR, "button[ng-click='consultar']"))).click()
        time.sleep(1)

    def _download_csv(self, driver: webdriver.Chrome, prefijo: str, name_date: str,
                      file_timeout: int = 120) -> Path:
        """Dispara la descarga, espera el .csv real y lo renombra.
        
        `prefijo` define el nombre final: `{prefijo}_{name_date}.csv`
        Así los archivos de cada servicio quedan sepparados por nombre y 
        los ciclos de descarga por servicio no confunden las descargas ya
        existentes."""
        wait = WebDriverWait(driver, 20)

        existing = set(self.download_dir.glob("*"))  # snapshot pre-descarga

        action_download = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "span[ng-csv = 'gerarCsvDesincorporacion()']")))
        driver.execute_script("arguments[0].click();", action_download)

        # -- Esperar un .csv real (sin parciales) ---
        elapsed = 0
        new_file = None
        while elapsed < file_timeout:
            new_files = set(self.download_dir.glob("*")) - existing
            has_partial = any(f.suffix in ('.crdownload', '.tmp') for f in new_files)
            real_csvs = [f for f in new_files if f.suffix == '.csv']
            if real_csvs and not has_partial:
                new_file = real_csvs[0]
                break
            time.sleep(1)
            elapsed += 1
        else:
            raise TimeoutError(
                f"[{prefijo}] La descarga no se completó en {file_timeout}s"
            )

        # -- Estabilización del tamaño ---
        previous_size = -1
        while True:
            current_size = new_file.stat().st_size
            if current_size == previous_size and current_size > 0:
                break
            previous_size = current_size
            time.sleep(0.5)

        target = self.download_dir / f"{prefijo}_{name_date}.csv"
        if target.exists():
            target.unlink()
        new_file.rename(target)
        print(f"[{prefijo}][OK] {target.name}")
        return target

    # -- Ciclo completo por servicio ----------------------
    def _run_report_cycle(self, driver: webdriver.Chrome, *,
                      servicio: str, prefijo: str,
                      i_date: str, f_date: str,
                      i_hour: str, f_hour: str,
                      name_date: str) -> Path:
        """Ejecuta un ciclo completo de reporte para un valor de 'Servicio.
        Orden: servicio -> intervalo -> consultar -> descargar. Autocontenido:
        no asume nada del estado que dejó el servicio anterior.'"""

        print(f"\n === Ciclo: {servicio} -> {prefijo}_{name_date}.csv ===")
        self._select_servicio(driver, servicio)
        self._set_date_hour_interval(driver, i_date, f_date, i_hour, f_hour)
        self._consultar(driver)
        return self._download_csv(driver, prefijo, name_date)
        


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
    def scrape(self) -> Path:
        """Scrape data from the Sonda website for the previous day."""
        target_date = yesterday_cdmx()
        date_str  = target_date.strftime("%d%m%Y")
        date_str_ = target_date.strftime("%d%m%y")

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            desinc_csv = self._run_report_cycle(driver,
                                                servicio=SERVICIO_DESINCORPORACION,
                                                prefijo="Desinc",
                                                i_date=date_str, f_date=date_str,
                                                i_hour='000000', f_hour='235959',
                                                name_date=date_str_,
                                                )

            time.sleep(1)
            self._logout(driver)
        finally:
            driver.quit()
        
        return desinc_csv    

# Bloque que permite test execution 
# En prompt invocas python -m extract.scrapers.Desincorporaciones
if __name__ == "__main__":
    scraper = Desincorporaciones_Scraper()
    scraper.run()