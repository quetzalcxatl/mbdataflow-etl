"""Connector for the Pasos report data source."""
from __future__ import annotations

import os
from pathlib import Path

import time
from datetime import datetime, timedelta
from selenium.webdriver.support.ui import Select
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from ..base import Extractor
from ..helpers.download_helper import get_latest_row_status

from config.settings  import (SONDA_QUERY_USER,
                              SONDA_QUERY_PASSWORD,
                              RAW_PASOS_PATH,
                              )


_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

class Pasos_Scraper(Extractor):
    """Download and load data for the Reporte_Pasos source."""

    name = "Pasos"

    # Constructor sub-method
    # Prepares the download directory
    def __init__(self, config_path: Path | None = None) -> None:
        self.download_dir = RAW_PASOS_PATH
        self.download_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_headless(is_cloud_run: bool) -> bool:
        "Resuelve/determina si Chrome corre headless. DESACOPLADO de la detección"
        "de Cloud Run"
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
        menu_item = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="navbar-fixed-left"]/ul/li[11]/ul/li/ul/li[2]/a[1]')))
        driver.save_screenshot(str(self.download_dir / "step8_menuitem_visible.png"))
        driver.execute_script("arguments[0].click();", menu_item)
        driver.save_screenshot(str(self.download_dir / "step9_menuitem_clicked.png"))
        # Wait for the report iframe to appear
        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)
        driver.save_screenshot(str(self.download_dir / "step10_Pasos_iframe.png")) # <- Checkpoint


    def _set_date(self, driver: webdriver.Chrome, button_id: str, target) -> None:
        """Selecciona una fecha en el datepicker de react-day-picker (shadcn).

        button_id: 'fecha-inicio' o 'fecha-fin'.
        target: datetime.date (o datetime — solo se usan y/m/d).

        Estrategia:
        1. Click al botón trigger para abrir el popover.
        2. Leer caption 'mes yyyy', navegar mes-a-mes con prev/next hasta llegar al mes/año destino.
        3. Click el <button name="day"> cuyo texto == target.day.
        4. Verificar post-condición: el <span> dentro del botón trigger muestra dd/mm/yyyy correcto.
        """
        wait = WebDriverWait(driver, 15)
        trigger = wait.until(EC.element_to_be_clickable((By.ID, button_id)))
        driver.execute_script("arguments[0].click();", trigger)

        # Popover abierto: esperar cualquier .rdp visible en el DOM
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.rdp")))

        def _current_caption() -> tuple[int, int]:
            """Devuelve (year, month) del calendario actualmente visible."""
            caption_el = driver.find_element(
                By.CSS_SELECTOR, "div.rdp [id^='react-day-picker-']"
            )
            raw = caption_el.text.strip().lower()  # ej. 'agosto 2026'
            try:
                mes_str, anio_str = raw.split()
                return int(anio_str), _MESES_ES[mes_str]
            except (ValueError, KeyError) as e:
                raise RuntimeError(f"Caption de calendario no parseable: {raw!r}") from e

        # Navegar mes-a-mes con cap de seguridad
        target_ym = (target.year, target.month)
        for _ in range(24):  # 24 iteraciones = 2 años, más que suficiente
            cur_year, cur_month = _current_caption()
            if (cur_year, cur_month) == target_ym:
                break
            # Delta signado: negativo → prev, positivo → next
            delta = (target.year - cur_year) * 12 + (target.month - cur_month)
            nav_name = "next-month" if delta > 0 else "previous-month"
            nav_btn = driver.find_element(By.CSS_SELECTOR, f"button[name='{nav_name}']")
            # Capturar caption previo para esperar cambio (más robusto que sleep)
            prev_caption_text = driver.find_element(
                By.CSS_SELECTOR, "div.rdp [id^='react-day-picker-']"
            ).text
            driver.execute_script("arguments[0].click();", nav_btn)
            wait.until(lambda d: d.find_element(
                By.CSS_SELECTOR, "div.rdp [id^='react-day-picker-']"
            ).text != prev_caption_text)
        else:
            raise RuntimeError(
                f"No se alcanzó {target_ym} tras 24 clicks de navegación en {button_id}"
            )

        # Click el día — button[name='day'] con texto exacto = target.day sin padding
        day_xpath = f"//div[contains(@class,'rdp')]//button[@name='day' and normalize-space(text())='{target.day}']"
        day_btn = wait.until(EC.element_to_be_clickable((By.XPATH, day_xpath)))
        driver.execute_script("arguments[0].click();", day_btn)

        # Post-condición: el span dentro del trigger debe mostrar dd/mm/yyyy
        expected = target.strftime("%d/%m/%Y")
        wait.until(lambda d: d.find_element(
            By.CSS_SELECTOR, f"button#{button_id} span"
        ).text.strip() == expected)

    def _set_hour_minute(self, driver: webdriver.Chrome, date_button_id: str,
                        hour: int, minute: int) -> None:
        """Setea los <select> de hora y minuto asociados al botón de fecha.

        Los dos selects son hermanos del button dentro del mismo wrapper
        <div class="flex items-center gap-1">. Van en orden: [hora, minuto].
        """
        from selenium.webdriver.support.ui import Select
        if not (0 <= hour <= 23):
            raise ValueError(f"hour fuera de rango [0,23]: {hour}")
        if not (0 <= minute <= 59):
            raise ValueError(f"minute fuera de rango [0,59]: {minute}")

        xpath = (
            f"//button[@id='{date_button_id}']"
            "/ancestor::div[contains(@class,'flex') and contains(@class,'items-center')][1]"
            "//select"
        )
        selects = driver.find_elements(By.XPATH, xpath)
        if len(selects) != 2:
            raise RuntimeError(
                f"Esperaba 2 selects (hora, minuto) hermanos de #{date_button_id}, "
                f"encontré {len(selects)}"
            )
        Select(selects[0]).select_by_value(f"{hour:02d}")
        Select(selects[1]).select_by_value(f"{minute:02d}")


    def _request_datetime_interval(self, driver, dt_i, dt_f) -> None:
        """Rellena fecha inicio, hora/min inicio, fecha fin, hora/min fin.

        dt_i, dt_f: datetime tz-aware. Los segundos se ignoran (granularidad
        del SPA es minuto). La selección de línea y el click de Consultar
        quedan fuera del scope de esta función.
        """
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.ID, "fecha-inicio")))

        self._set_date(driver, "fecha-inicio", dt_i.date())
        self._set_hour_minute(driver, "fecha-inicio", dt_i.hour, dt_i.minute)

        self._set_date(driver, "fecha-fin", dt_f.date())
        self._set_hour_minute(driver, "fecha-fin", dt_f.hour, dt_f.minute)

        driver.save_screenshot(str(self.download_dir / "step11_datetime_interval.png"))
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


    #---------------------------------Scrape_Method------------------------------------------
    def scrape(self) -> None:
        from utils.dates import last_completed_operational_week_cdmx, last_operational_week_number
        start, end = last_completed_operational_week_cdmx()
        week_numero = last_operational_week_number()

        # El SPA de Pasos tiene granularidad de MINUTO (no segundos como Viaje).
        # Contrato 03:20 → 03:19 semiabierto en granularidad minuto.
        dt_i = start.replace(second=0, microsecond=0)
        dt_f = (end - timedelta(minutes=1)).replace(second=0, microsecond=0)

        print(f"Semana operativa: {start.strftime('%Y-%m-%d %H:%M %Z')} .. "
            f"{end.strftime('%Y-%m-%d %H:%M %Z')}")
        print(f"Filtro Pasos (semiabierto min): "
            f"{dt_i.strftime('%Y-%m-%d %H:%M')} .. {dt_f.strftime('%Y-%m-%d %H:%M')}")

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            self._request_datetime_interval(driver, dt_i, dt_f)
            # Selección de línea y Consultar: fuera del scope de PR-B
            time.sleep(2)
            self._logout(driver)
        finally:
            driver.quit()

        return None


# Bloque que permite test execution 
# En prompt invocas python -m extract.scrapers.Pasos
if __name__ == "__main__":
    scraper = Pasos_Scraper()
    scraper.run()