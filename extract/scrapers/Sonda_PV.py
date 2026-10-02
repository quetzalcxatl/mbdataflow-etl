"""Connector for the Sonda PV (Programado vs Real por jornada) report."""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..base import Extractor

from config.settings import (SONDA_QUERY_USER,
                             SONDA_QUERY_PASSWORD,
                             RAW_PV_PATH,
                             SONDA_PV_CONFIG_PATH,
                             )

# Orden de descarga y nombre de cada turno. Las horas vienen de sonda_pv_config.json.
TURNOS = ("Matutino", "Vespertino")
TURNO_CONFIG_KEY = {
    "Matutino":   "Franja_Horaria_Matutino",
    "Vespertino": "Franja_Horaria_Vespertino",
}

# Campo del formulario que identifica al iframe del reporte PV
HORA_INPUT_CSS = "input[ng-model='filter.hora']"
XPATH_MENU_PV = '//*[@id="navbar-fixed-left"]/ul/li[2]/ul/li/ul/li[13]/a[1]'

# Extensiones de Chrome mientras la descarga no ha terminado
PARTIAL_SUFFIXES = (".crdownload", ".tmp")


class SondaPV_Scraper(Extractor):
    """Descarga los reportes PV Matutino y Vespertino del día.

    A diferencia de Viaje/Pasos, el reporte PV NO pasa por la Central de
    Descargas: el botón 'relatorioJornada()' descarga el CSV directamente.
    Por eso no compite por la cola de solicitudes de la cuenta (§5.10).

    El reporte siempre es del día en curso en Sonda; `fecha` solo etiqueta los
    archivos. El pipeline la pasa desde utils.dates.today_cdmx() — una sola
    lectura del reloj compartida por extract, transform y load.
    """

    name = "Sonda_PV"

    def __init__(self, fecha: date, config_path: Path | None = None) -> None:
        self.fecha = fecha
        self.download_dir = RAW_PV_PATH
        self.download_dir.mkdir(parents=True, exist_ok=True)

        with open(config_path or SONDA_PV_CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.hours = {turno: cfg[TURNO_CONFIG_KEY[turno]] for turno in TURNOS}

    @staticmethod
    def _resolve_headless(is_cloud_run: bool) -> bool:
        """Resuelve si Chrome corre headless.

        En Cloud Run NO hay display: headless es obligatorio y no se puede
        anular. `SCRAPER_HEADLESS` solo tiene efecto en local, donde sirve
        para ver el navegador durante el debug del SPA.

        Precedencia:
          1. Cloud Run detectado      -> True, sin excepción.
          2. SCRAPER_HEADLESS seteada -> lo que diga (solo local).
          3. Default                  -> True.
        """
        if is_cloud_run:
            return True

        override = os.environ.get("SCRAPER_HEADLESS")
        if override is not None:
            return override.strip().lower() in ("1", "true", "yes", "on")
        return True

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
        # Silencia el ruido de logs de Chrome en Windows (updater, GCM...)
        options.add_experimental_option("excludeSwitches", ["enable-logging"])

        if headless:
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1366,768")
            driver = webdriver.Chrome(options=options)
            # Headless Chrome ignora las download prefs — hay que usar CDP.
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

    def _document_state(self, driver: webdriver.Chrome) -> str:
        """Resumen del documento actual para el log. Nunca lanza.

        El estado va al log, no al disco: en Cloud Run el filesystem se
        evapora, stdout sobrevive en Cloud Logging.
        """
        try:
            return driver.execute_script(
                "return document.readyState"
                " + ' | url=' + document.location.href"
                " + ' | inputs=' + document.querySelectorAll('input').length"
                " + ' | iframes=' + document.querySelectorAll('iframe').length"
            )
        except Exception:
            return "no se pudo inspeccionar el documento"

    # Sub-método privado
    # Proceso de logeado en la página de Sinoptico
    def _login(self, driver: webdriver.Chrome) -> None:
        driver.get("https://cdmx.sinopticoplus.com/#/")
        wait = WebDriverWait(driver, 60)
        try:
            username_input = wait.until(EC.presence_of_element_located((By.NAME, "login")))
            password_input = wait.until(EC.presence_of_element_located((By.NAME, "password")))
            username_input.send_keys(SONDA_QUERY_USER)
            password_input.send_keys(SONDA_QUERY_PASSWORD)
            login_btn = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[type='submit']")))
            login_btn.click()
        except Exception as e:
            raise RuntimeError(
                f"Login form not found. Estado del documento: {self._document_state(driver)}"
            ) from e

    # Navegamos al reporte PV
    def _navigate_to_report(self, driver: webdriver.Chrome) -> None:
        wait = WebDriverWait(driver, 30)
        sidebar_icon = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "img[src='img/fa-list.png']")))
        driver.execute_script("arguments[0].click();", sidebar_icon)

        menu_item = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_MENU_PV)))
        driver.execute_script("arguments[0].click();", menu_item)

        self._enter_report_iframe(driver)

    def _enter_report_iframe(self, driver: webdriver.Chrome, timeout: int = 60) -> None:
        """Entra al iframe que contiene el formulario del reporte PV.

        El <iframe> aparece en el DOM antes de que su documento cargue, y puede
        haber más de uno: se recorren todos hasta encontrar el campo de hora.
        Entrar con By.TAG_NAME al primer iframe dejaba a Selenium sobre un
        documento vacío (TimeoutException en filter.hora, validado en local).
        """
        deadline = time.monotonic() + timeout
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            driver.switch_to.default_content()
            for index in range(len(driver.find_elements(By.TAG_NAME, "iframe"))):
                driver.switch_to.default_content()
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                if index >= len(iframes):
                    break
                driver.switch_to.frame(iframes[index])
                if driver.find_elements(By.CSS_SELECTOR, HORA_INPUT_CSS):
                    print(f"[IFRAME] Formulario PV en iframe #{index} (intento {attempt})")
                    return
            time.sleep(2)

        driver.switch_to.default_content()
        iframes_info = driver.execute_script(
            "return Array.from(document.querySelectorAll('iframe')).map((f, i) =>"
            " i + ': id=' + f.id + ' src=' + f.getAttribute('src')"
            " + ' ng-src=' + f.getAttribute('ng-src'));"
        )
        raise TimeoutError(
            f"No apareció {HORA_INPUT_CSS} en ningún iframe tras {timeout}s. "
            f"Iframes: {iframes_info or '(ninguno)'}. "
            f"Estado: {self._document_state(driver)}"
        )

    def _new_complete_files(self, existing: set[Path]) -> list[Path]:
        return [
            p
            for p in set(self.download_dir.glob("*")) - existing
            if p.is_file() and not p.name.endswith(PARTIAL_SUFFIXES)
        ]

    def _download_for_turno(self, driver: webdriver.Chrome, turno: str,
                            file_timeout: int = 60) -> Path:
        wait = WebDriverWait(driver, 30)
        hour = self.hours[turno]

        time_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, HORA_INPUT_CSS)))
        time_input.clear()
        time_input.send_keys(hour)

        existing = set(self.download_dir.glob("*"))  # snapshot pre-descarga
        wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "button[ng-click='relatorioJornada()']"))).click()
        print(f"[{turno}][DOWNLOAD] Solicitado reporte con hora {hour}")

        # --- Esperar un archivo nuevo y completo (sin parciales) ---
        try:
            WebDriverWait(driver, file_timeout).until(lambda d: self._new_complete_files(existing))
        except Exception as e:
            raise TimeoutError(
                f"[{turno}] La descarga no completó en {file_timeout}s. "
                f"Estado: {self._document_state(driver)}"
            ) from e
        new_file = self._new_complete_files(existing)[0]

        # --- Estabilización de tamaño ---
        previous_size = -1
        while True:
            current_size = new_file.stat().st_size
            if current_size == previous_size and current_size > 0:
                break
            previous_size = current_size
            time.sleep(0.5)

        # --- Rename al contrato de nomenclatura ---
        target = self.download_dir / f"PV_{self.fecha.strftime('%Y%m%d')}_{turno}.csv"
        new_file.replace(target)  # sobrescribe si ya existe
        print(f"[{turno}][OK] {target.name} ({current_size} bytes)")
        return target

    # Make logout of Sonda platform
    def _logout(self, driver: webdriver.Chrome) -> None:
        wait = WebDriverWait(driver, 20)
        driver.switch_to.default_content()

        sidebar_icon = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "img[src='img/fa-list.png']")))
        driver.execute_script("arguments[0].click();", sidebar_icon)

        logout_icon = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[ng-click='logout()']")))
        driver.execute_script("arguments[0].click();", logout_icon)

        logout_confirm = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[class='confirm confirm-btn']")))
        logout_confirm.click()

    #---------------------------------Scrape_Method------------------------------------------
    def scrape(self) -> dict[str, Path]:
        """Descarga ambos turnos. Retorna {turno: Path del CSV crudo}."""
        print(f"Fecha PV: {self.fecha} · horas: {self.hours}")
        downloaded: dict[str, Path] = {}

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            for turno in TURNOS:
                downloaded[turno] = self._download_for_turno(driver, turno)

            # Logout best-effort: los CSV ya están en disco. Un fallo aquí no
            # debe tirar la carga del día; queda en el log para revisarlo.
            try:
                self._logout(driver)
            except Exception as e:
                print(f"[LOGOUT] falló (ignorado, descargas completas): "
                      f"{type(e).__name__}: {e}")
        finally:
            driver.quit()

        return downloaded


# Bloque que permite test execution
# En prompt invocas python -m extract.scrapers.Sonda_PV
if __name__ == "__main__":
    from utils.dates import today_cdmx
    scraper = SondaPV_Scraper(today_cdmx().date())
    print(scraper.run())
