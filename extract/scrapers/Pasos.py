"""Connector for the Pasos report data source."""
from __future__ import annotations

import os
from pathlib import Path

import time
from datetime import datetime, timedelta
from selenium.common.exceptions import TimeoutException
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

LINEAS_OPERATIVAS = [
    'Línea 1', 'Línea 2', 'Línea 3', 'Línea 4', 'Línea 5',
    'Línea 6', 'Línea 7', 'Línea A31', 'Línea C21', 'Línea H72',
]

LINEA_SLUG = {
    'Línea 1' : 'L1',
    'Línea 2' : 'L2',
    'Línea 3' : 'L3',
    'Línea 4' : 'L4',
    'Línea 5' : 'L5',
    'Línea 6' : 'L6',
    'Línea 7' : 'L7',
    'Línea A31' : 'A31',
    'Línea C21' : 'C21',
    'Línea H72' : 'H72',
}

CENTRAL_IFRAME_CSS = "iframe[ng-src='#/preferencias/central']"
XPATH_MENU_CENTRAL = "//*[@id='navbar-fixed-left']/ul/li[9]/ul/li/ul/li[1]/a[1]"

class Pasos_Scraper(Extractor):
    """Download and load data for the Reporte_Pasos source."""

    name = "Pasos"

    # Constructor sub-method
    # Prepares the download directory
    def __init__(self, config_path: Path | None = None) -> None:
        self.download_dir = RAW_PASOS_PATH
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self._pasos_iframe_id: str | None = None

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
        # Capturamos el id del iframe de Pasos para poder volver a él de forma
        # determinista tras visitar la Central (que monta un iframe adicional).
        self._pasos_iframe_id = iframe.get_attribute("id")
        if not self._pasos_iframe_id:
            raise RuntimeError("El iframe del reporte de Pasos no tiene atributo id")
        print(f"[IFRAME] Pasos montado en #{self._pasos_iframe_id}")
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

    def _select_linea(self, driver: webdriver.Chrome, linea_name: str) -> None:
        """Selecciona una línea del dropdown 'Línea' (custom shadcn dropdown).

        Estructura:
        - Trigger: <div class='... cursor-pointer w-[200px]'> dentro del bloque
            cuya label es 'Línea *'.
        - Popover: <div class='absolute z-50 ...'> con <input placeholder='Buscar...'>
            y opciones <div class='... cursor-pointer'> con <div class='truncate'>TEXTO</div>.

        Post-condición: el <span class='truncate'> del trigger muestra linea_name.
        """
        wait = WebDriverWait(driver, 15)

        trigger_xpath = (
            "//label[starts-with(normalize-space(.), 'Línea')]"
            "/following-sibling::div[1]//div[contains(@class,'cursor-pointer')]"
        )
        trigger = wait.until(EC.element_to_be_clickable((By.XPATH, trigger_xpath)))
        driver.execute_script("arguments[0].click();", trigger)

        # Popover identificado por el input 'Buscar...' (discriminador robusto)
        popover_xpath = (
            "//div[contains(@class,'absolute') and contains(@class,'z-50')"
            " and .//input[@placeholder='Buscar...']]"
        )
        wait.until(EC.presence_of_element_located((By.XPATH, popover_xpath)))

        # Opción: el div outer con cursor-pointer que contiene el texto exacto.
        # Clickeamos el outer (no el .truncate) para que el evento se maneje correctamente.
        option_xpath = (
            f"{popover_xpath}//div[contains(@class,'cursor-pointer')"
            f" and .//div[normalize-space(text())='{linea_name}']]"
        )
        option = wait.until(EC.element_to_be_clickable((By.XPATH, option_xpath)))
        driver.execute_script("arguments[0].click();", option)

        # Post-condición: el span del trigger muestra la línea seleccionada
        trigger_span_xpath = (
            "//label[starts-with(normalize-space(.), 'Línea')]"
            "/following-sibling::div[1]//span[contains(@class,'truncate')]"
        )
        wait.until(lambda d: d.find_element(
            By.XPATH, trigger_span_xpath
        ).text.strip() == linea_name)
        driver.save_screenshot(str(self.download_dir / "step12_select_linea.png"))

    def _click_descargar_reporte(self, driver: webdriver.Chrome,
                             timeout: int = 20) -> None:
        """Click al botón 'Descargar reporte'.

        Precondición: Línea seleccionada (es lo que habilita el botón).
        'Consultar' no interviene en el flujo — este SPA descarga directamente.

        Timeout generoso (60s) porque desconocemos empíricamente cuánto tarda el
        SPA en habilitar el botón tras la selección de línea (puede haber round-trip
        al backend para validar).
        """
        wait = WebDriverWait(driver, timeout)
        boton = wait.until(EC.element_to_be_clickable((
            By.XPATH, "//button[normalize-space(.)='Descargar reporte']"
        )))
        driver.execute_script("arguments[0].click();", boton)
        driver.save_screenshot(str(self.download_dir / "step13_Descargar_clicked.png"))

    def _confirm_download_modal(self, driver: webdriver.Chrome,
                            timeout: int = 15) -> None:
        """Confirma el modal 'Descargar reporte' clickeando 'Guardar'.

        El modal es un Radix Dialog (role='dialog', data-state='open') que
        aparece tras clickear 'Descargar reporte'. El texto indica que el
        reporte se encolará en la Central de Descargas — el click en 'Guardar'
        NO descarga un archivo, solo envía la solicitud.

        Post-condición: el dialog desaparece del DOM (o pasa a data-state='closed').
        """
        wait = WebDriverWait(driver, timeout)

        wait.until(EC.presence_of_element_located((
            By.XPATH, "//div[@role='dialog' and @data-state='open']"
        )))

        guardar = wait.until(EC.element_to_be_clickable((
            By.XPATH,
            "//div[@role='dialog' and @data-state='open']"
            "//button[normalize-space(.)='Guardar']"
        )))
        driver.execute_script("arguments[0].click();", guardar)

        wait.until(EC.invisibility_of_element_located((
            By.XPATH, "//div[@role='dialog' and @data-state='open']"
        )))
        driver.save_screenshot(str(self.download_dir / "step14_report_confirmed.png"))

    def _focus_pasos_form(self, driver: webdriver.Chrome) -> None:
        """Vuelve al iframe del formulario de Pasos.

        El formulario sobrevive con fechas/horas puestas mientras su ventana
        no se cierre — no hay que re-setear nada, solo re-entrar al iframe.
        """
        if not self._pasos_iframe_id:
            raise RuntimeError("_pasos_iframe_id no inicializado — "
                                "¿se llamó _navigate_to_report?")
        wait = WebDriverWait(driver, 20)
        driver.switch_to.default_content()
        iframe = wait.until(EC.presence_of_element_located(
            (By.ID, self._pasos_iframe_id)))
        driver.switch_to.frame(iframe)
        driver.save_screenshot(str(self.download_dir / "step16_pasos_form_focused.png"))



    def _navigate_to_downloads(self, driver: webdriver.Chrome,
                           timeout: int = 60) -> None:
        """Abre la Central de Descargas y entra a su iframe, ya booteado.

        El iframe de la Central tiene src hash-relativo ('#/preferencias/central'),
        lo que hace que arranque la SPA de Sonda completa desde cero. El elemento
        <iframe> existe en el DOM desde que jQuery UI crea el ui-dialog — mucho
        antes de que su documento interno esté listo. Entrar sin gatear la
        disponibilidad deja a Selenium operando sobre el documento inicial vacío.
        """
        wait = WebDriverWait(driver, 30)
        driver.switch_to.default_content()

        sidebar_icon = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "img[src='img/fa-list.png']")))
        driver.execute_script("arguments[0].click();", sidebar_icon)

        menu_central = wait.until(EC.presence_of_element_located(
            (By.XPATH, XPATH_MENU_CENTRAL)))
        driver.execute_script("arguments[0].click();", menu_central)

        self._enter_central_iframe(driver, timeout=timeout)
        time.sleep(4)
        driver.save_screenshot(str(self.download_dir / "step15_Central_Descargas_visible.png"))


    def _enter_central_iframe(self, driver: webdriver.Chrome,
                            timeout: int = 60) -> None:
        """Entra al iframe de la Central esperando a que su documento esté listo.

        Reintenta el switch: si entramos al documento inicial (pre-navegación),
        salimos, re-localizamos el iframe y volvemos a entrar. Cada intento gatea
        sobre readyState + presencia del formulario de consulta.
        """
        deadline = time.monotonic() + timeout
        attempt = 0
        last_state = "desconocido"

        while time.monotonic() < deadline:
            attempt += 1
            driver.switch_to.default_content()
            iframe = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, CENTRAL_IFRAME_CSS)))
            driver.switch_to.frame(iframe)

            try:
                WebDriverWait(driver, 10).until(
                    lambda d: d.execute_script("return document.readyState") == "complete")
                WebDriverWait(driver, 10).until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "button[type=submit]")))
                print(f"[CENTRAL] Iframe listo en el intento {attempt}")
                return
            except TimeoutException:
                # Capturar en qué quedó el documento interno para el mensaje de error
                last_state = driver.execute_script(
                    "return document.readyState + ' | url=' + document.location.href"
                    " + ' | iframes_anidados=' + document.querySelectorAll('iframe').length"
                    " + ' | submit_btns=' + document.querySelectorAll('button[type=submit]').length"
                )
                print(f"[CENTRAL] Intento {attempt} sin éxito — {last_state}")
                time.sleep(2)

        raise TimeoutError(
            f"El iframe de la Central no expuso 'button[type=submit]' en {timeout}s. "
            f"Último estado interno: {last_state}"
        )
    
    
    '''
    def _navigate_to_downloads(self, driver: webdriver.Chrome) -> None:
        """Abre la Central de Descargas y entra a su iframe.

        Sale del iframe de Pasos (default_content) sin cerrar su ventana, abre
        la Central desde el sidebar, y entra al iframe correcto por ng-src.
        """
        wait = WebDriverWait(driver, 30)
        driver.switch_to.default_content()

        sidebar_icon = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "img[src='img/fa-list.png']")))
        driver.execute_script("arguments[0].click();", sidebar_icon)

        menu_central = wait.until(EC.presence_of_element_located(
            (By.XPATH, XPATH_MENU_CENTRAL)))
        driver.execute_script("arguments[0].click();", menu_central)

        # Locator PRECISO — no By.TAG_NAME 'iframe': el multiaba mantiene vivo
        # el iframe de Pasos y el primer match sería ese, no la Central.
        iframe = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, CENTRAL_IFRAME_CSS)))
        driver.switch_to.frame(iframe)
        time.sleep(4)
        driver.save_screenshot(str(self.download_dir / "step16_Central_Descargas_visible.png"))
    '''

    def _close_downloads_window(self, driver: webdriver.Chrome) -> None:
        """Cierra el ui-dialog de la Central de Descargas.

        Deja el DOM en el mismo estado que antes de abrirla, de modo que cada
        iteración del ciclo arranque desde condiciones idénticas.
        """
        wait = WebDriverWait(driver, 20)
        driver.switch_to.default_content()

        close_btn = wait.until(EC.element_to_be_clickable((
            By.XPATH,
            "//div[contains(@class,'ui-dialog')]"
            "[.//iframe[@ng-src='#/preferencias/central']]"
            "//button[contains(@class,'ui-dialog-titlebar-close')]"
        )))
        driver.execute_script("arguments[0].click();", close_btn)
        wait.until(EC.invisibility_of_element_located(
            (By.CSS_SELECTOR, CENTRAL_IFRAME_CSS)))

    def _download_latest(self, driver: webdriver.Chrome, linea_slug: str,
                     week_number: int, date_name: str,
                     status_timeout: int = 600) -> Path:
        """Pollea la Central hasta COMPLETO y descarga la fila más reciente.

        Precondición: estamos dentro del iframe de la Central y acabamos de
        encolar exactamente una solicitud — por eso 'la fila más reciente' es
        inequívocamente la nuestra (ventaja del ciclo completo por línea).

        status_timeout: cap de wall-clock para la generación del reporte.
        Sin él, un reporte atorado en EN PROGRESO consumiría el task-timeout
        completo del Cloud Run Job sin log accionable.
        """
        wait = WebDriverWait(driver, 30)
        table_css = "table#example.table.responsive.tPainelEventos"
        deadline = time.monotonic() + status_timeout

        def click_query_button():
            """Re-localiza y clickea el botón de consulta en cada llamada
            para evitar referencias stale tras el re-render de Angular."""
            btn = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[type=submit]")))
            driver.execute_script("arguments[0].click();", btn)

        click_query_button()
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, table_css)))
        time.sleep(1)  # deja terminar el render del ng-repeat

        # --- Polling de status ---
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"[{linea_slug}] La Central no reportó COMPLETO en "
                    f"{status_timeout}s"
                )

            result = get_latest_row_status(driver, wait)
            status = result['status']
            print(f"[{linea_slug}][STATUS] {status}")

            if status == 'COMPLETO':
                break
            elif status in ('EN PROGRESO', 'ESPERANDO INICIO'):
                time.sleep(10)
                click_query_button()
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, table_css)))
            else:
                raise RuntimeError(
                    f"[{linea_slug}] Sonda reportó status inesperado: {status}"
                )

        # --- Disparar la descarga ---
        rows = driver.find_elements(By.CSS_SELECTOR, "table.tPainelEventos tbody tr")
        latest_row = rows[result['latest_row_index']]
        latest_date = datetime.strptime(result['latest_date'], "%d/%m/%Y %H:%M:%S")

        existing = set(self.download_dir.glob("*"))  # snapshot pre-descarga
        download_link = latest_row.find_element(By.CSS_SELECTOR, "a.btn-links")
        driver.execute_script("arguments[0].click();", download_link)
        print(f"[{linea_slug}][DOWNLOAD] Disparada descarga del reporte fechado "
            f"{latest_date.strftime('%d/%m/%Y %H:%M:%S')}")

        # --- Esperar un .csv real (sin parciales) ---
        file_timeout = 120
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
                f"[{linea_slug}] La descarga no completó en {file_timeout}s"
            )

        # --- Estabilización de tamaño ---
        previous_size = -1
        while True:
            current_size = new_file.stat().st_size
            if current_size == previous_size and current_size > 0:
                break
            previous_size = current_size
            time.sleep(0.5)

        # --- Rename al contrato de nomenclatura ---
        target = self.download_dir / f"pasos_{linea_slug}_sem{week_number}_{date_name}.csv"
        if target.exists():
            target.unlink()
        new_file.rename(target)
        print(f"[{linea_slug}][OK] {target.name}")
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
    def scrape(self) -> list[Path]:
        from utils.dates import last_completed_operational_week_cdmx, last_operational_week_number
        start, end = last_completed_operational_week_cdmx()
        week_numero = last_operational_week_number()

        # El SPA de Pasos tiene granularidad de MINUTO (no segundos como Viaje).
        # Contrato 03:20 → 03:19 semiabierto en granularidad minuto.
        dt_i = start.replace(second=0, microsecond=0)
        dt_f = (end - timedelta(minutes=1)).replace(second=0, microsecond=0)

        name_date = f"{start.strftime('%d%m%y')}_{end.strftime('%d%m%y')}"
        downloaded: list[Path] = []

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            self._request_datetime_interval(driver, dt_i, dt_f)

            # Ciclo completo por línea (Opción B): encolar → Central → esperar
            # → descargar → cerrar Central → volver al formulario → siguiente.
            # El formulario sobrevive con fechas/horas puestas: no se re-setea.
            for idx, linea in enumerate(LINEAS_OPERATIVAS, start=1):
                slug = LINEA_SLUG[linea]
                print(f"\n=== [{idx}/{len(LINEAS_OPERATIVAS)}] {linea} ({slug}) ===")

                self._select_linea(driver, linea)
                self._click_descargar_reporte(driver)
                self._confirm_download_modal(driver)
                print(f"[{slug}][ENCOLADO] Solicitud enviada")

                self._navigate_to_downloads(driver)
                target = self._download_latest(driver, slug, week_numero, name_date)
                downloaded.append(target)

                self._close_downloads_window(driver)
                self._focus_pasos_form(driver)

            print(f"\n[RESUMEN] {len(downloaded)}/{len(LINEAS_OPERATIVAS)} "
                f"reportes descargados")

            self._logout(driver)
        finally:
            driver.quit()

        return downloaded


# Bloque que permite test execution 
# En prompt invocas python -m extract.scrapers.Pasos
if __name__ == "__main__":
    scraper = Pasos_Scraper()
    scraper.run()