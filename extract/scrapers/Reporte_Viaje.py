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

    @staticmethod
    def _resolve_headless(is_cloud_run: bool) -> bool:
        "Resuelve/determina si Chrome corre headless. DESACOPLADO de la detección"
        "de Cloud Run"
        override = os.environ.get("SCRAPER_HEADLESS")
        if override is not None:
            return override.strip().lower() in ("1", "true", "yes", "on")
        return is_cloud_run
        
    # Private sub-method
    # Instanciate Chrome Webdriver throught Selenium package
    def _start_driver(self) -> webdriver.Chrome:
        options = Options()
        is_cloud_run = any(k in os.environ for k in ("CLOUD_RUN_JOB", "K_SERVICE", "CLOUD_RUN_EXECUTION"))
        headless = self._resolve_headless(is_cloud_run)
        print(f"[DRIVER] headless={headless} cloud_run={is_cloud_run} "
              f"download_dir={self.download_dir}")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        
        if headless:
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1366,768")
            driver = webdriver.Chrome(options=options)
            # Headless Chrome ignores download prefs — must use CDP
            driver.execute_cdp_cmd(
                "Page.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(self.download_dir)},
            )
        else:
            prefs = {"download.default_directory": str(self.download_dir)}
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
        d: datetime.date o datetime.datetime. Solo se usan y/m/d — la hora, si viene
        en un datetime, se ignora porque el datepicker es de fecha, no de instante.
        El Date se construye desde partes locales para evitar el corrimiento UTC del
        Chrome headless en Cloud Run."""
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
        driver.save_screenshot(str(self.download_dir / "step15a_guardar_buttom_visible.png"))
        driver.execute_script("arguments[0].click();", guardar_buttom)
        driver.save_screenshot(str(self.download_dir / "step15b_guardar_buttom_clicked.png"))

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
        driver.save_screenshot(str(self.download_dir / "step16_download_dashboard.png"))

        return None
    
    #------------------------------------------------------------------------------------------------------------------------

    # Download method
    def _download(self, driver: webdriver.Chrome, date_name: str, week_number: int)-> Path:
        wait = WebDriverWait(driver, 30)

        def click_query_button():
            """Re-locates and clicks the query button fresh each time to avoid stale references."""
            btn = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[type=submit]")
            ))
            driver.execute_script("arguments[0].click();", btn)

        # --- Trigger the first query to populate the table ---
        click_query_button()
        time.sleep(1)
        driver.save_screenshot(str(self.download_dir / "step17_request.png"))

        # --- Wait for the table to appear ---
        wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "table#example.table.responsive.tPainelEventos")
        ))
        driver.save_screenshot(str(self.download_dir / "step18_table_visible.png"))
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
                driver.save_screenshot(str(self.download_dir / "step19_download_succesfull.png")) 
                
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
                target = self.download_dir / f"RV_sem{week_number}_{date_name}.csv"
                if target.exists():
                    target.unlink()
                new_file.rename(target)
                return target

            elif status in ('EN PROGRESO', 'ESPERANDO INICIO'):
                print(f"[STATUS] {status}")
                # Report is still being generated — wait and re-trigger the table refresh
                time.sleep(10)
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

    # ------------------------------------------------------------------
    # Observabilidad de fallos (PR-A)
    # ------------------------------------------------------------------
    def _dump_debug_on_failure(self, driver) -> None:
        """Vuelca evidencia del fallo. Contrato: NUNCA lanza.

        Si propagara, enmascararía la excepción real (el bug que cazamos). Es la
        única excepción justificada a 'nada silencioso' (§5.6): aquí el silencio
        protege la señal, no la oculta. Todo se imprime a stdout (sobrevive vía
        Cloud Logging) y se exfiltra a Drive (sobrevive a la muerte del contenedor).
        """
        try:
            print("[DEBUG] -- captura de evidencia del fallo --------------------")
            try:
                print(f"[DEBUG] URL en el fallo: {driver.current_url}")
            except Exception as e:
                print(f"[DEBUG] current_url no disponible: {type(e).__name__}: {e}")

            # page_source del CONTEXTO ACTUAL. En el timeout de la tabla de descargas
            # estamos dentro del iframe -> es justo el DOM donde 'table#example'
            # debería existir (o no). Eso disambigua H1 vs H2.
            try:
                src = driver.page_source
                (self.download_dir / "FAILURE_page_source.html").write_text(src, encoding="utf-8")
                print(f"[DEBUG] page_source capturado: {len(src)} chars")
            except Exception as e:
                print(f"[DEBUG] page_source no disponible: {type(e).__name__}: {e}")

            try:
                driver.save_screenshot(str(self.download_dir / "FAILURE_final_state.png"))
            except Exception as e:
                print(f"[DEBUG] screenshot final no disponible: {type(e).__name__}: {e}")

            artifacts = sorted(p for p in self.download_dir.glob("*") if p.is_file())
            print(f"[DEBUG] {len(artifacts)} artefactos en {self.download_dir}:")
            for p in artifacts:
                print(f"[DEBUG]   {p.name} ({p.stat().st_size} bytes)")

            self._upload_debug_artifacts(artifacts)
        except Exception as e:
            print(f"[DEBUG] dumper falló (ignorado): {type(e).__name__}: {e}")

    def _upload_debug_artifacts(self, artifacts) -> None:
        """Exfiltra los artefactos de /tmp a Drive. Best-effort.

        Kill switch: SCRAPER_DEBUG_ARTIFACTS=false lo desactiva.
        Folder: DRIVE_VIAJE_DEBUG_FOLDER_ID si existe; si no, cae en
        DRIVE_VIAJE_FOLDER_ID (la misma carpeta del CSV).
        """
        if os.environ.get("SCRAPER_DEBUG_ARTIFACTS", "true").strip().lower() != "true":
            print("[DEBUG] SCRAPER_DEBUG_ARTIFACTS != true -> sin exfiltración.")
            return
        if not artifacts:
            print("[DEBUG] sin artefactos que exfiltrar.")
            return

        from config.settings import DRIVE_VIAJE_FOLDER_ID
        folder = os.environ.get("DRIVE_VIAJE_DEBUG_FOLDER_ID") or DRIVE_VIAJE_FOLDER_ID
        if not folder:
            print("[DEBUG] sin folder de Drive configurado -> sin exfiltración.")
            return

        try:
            from load.loaders.debug_drive_loader import upload_debug_files
            tag = datetime.now().strftime("%Y%m%d_%H%M%S")  # UTC en Cloud; solo es etiqueta
            ids = upload_debug_files(artifacts, folder, name_prefix=f"viaje_debug_{tag}__")
            print(f"[DEBUG] {len(ids)}/{len(artifacts)} artefactos exfiltrados a Drive folder {folder}")
        except Exception as e:
            print(f"[DEBUG] exfiltración a Drive falló (ignorado): {type(e).__name__}: {e}")

    def scrape(self) -> None:
        """Scrape data from Sonda para la última semana operativa completa.

        Contrato de "semana operativa" (ver utils.dates.last_completed_operational_week_cdmx):
        lunes 03:20 → lunes siguiente 03:20, en CDMX. Los viajes de la
        madrugada del lunes cuentan como servicio de la semana anterior.

        Cómo se traduce al filtro de Sonda:
          * date_i = lunes previo         (fecha inicial del rango de calendario)
          * date_f = lunes de la semana   (fecha final del rango de calendario, 8 días)
          * hora_i = "032000"             (hora del corte inicial)
          * hora_f = "031959"             (un segundo antes del corte final → semiabierto)

        Sonda interpreta el filtro fecha+hora como un rango CONTINUO
        [date_i hora_i, date_f hora_f] sobre el instante — no como una ventana
        horaria diaria. Con esta parametrización el filtro devuelve exactamente
        la semana operativa [lunes 03:20, próximo lunes 03:20).

        Nombre de archivo: RV_{ddmmyy_start}_{ddmmyy_end}.csv, i.e. 8 fechas de
        calendario (lun→lun), distinto del contrato viejo que usaba 7 (lun→dom).
        """
        from utils.dates import last_completed_operational_week_cdmx, last_operational_week_number
        start, end = last_completed_operational_week_cdmx()
        week_numero = last_operational_week_number()

        # Sonda recibe fecha (para el datepicker) y hora (texto) por separado.
        # start/end son datetimes tz-aware, pero al datepicker solo van y/m/d.
        date_i = start.date()
        date_f = end.date()

        # Constantes del contrato — no derivadas para dejar la semántica visible
        # en el sitio donde el operador la va a leer.
        hora_i = "032000"    # start.strftime("%H%M%S")
        hora_f = "031959"    # semiabierto: un segundo antes de end.strftime("%H%M%S")

        name_date = f"{start.strftime('%d%m%y')}_{end.strftime('%d%m%y')}"

        print(f"Semana operativa: {start.strftime('%Y-%m-%d %H:%M %Z')} .. "
              f"{end.strftime('%Y-%m-%d %H:%M %Z')}")
        print(f"Filtro Sonda: fechas {date_i}..{date_f}, horas {hora_i}..{hora_f}")

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            self._request_hourdate_interval(driver, date_i, date_f, hora_i, hora_f)
            self._navigate_to_downloads(driver)
            target_path = self._download(driver,name_date,week_numero)

            time.sleep(1)
            self._logout(driver)
        except Exception:
            # Captura ANTES de quit(): necesitamos el driver vivo. El dumper nunca
            # lanza, así que la excepción real se re-propaga intacta.
            self._dump_debug_on_failure(driver)
            raise

        finally:
            driver.quit()

        return target_path
    
# Bloque que permite test execution 
# En prompt invocas python -m extract.scrapers.Reporte_Viaje
if __name__ == "__main__":
    scraper = Viaje_Scraper()
    scraper.run()