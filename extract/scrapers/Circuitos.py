"""
Scraping process for the Ocurrencia de Circuitos report data source.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from utils.dates import last_completed_week_cdmx
from ..base import Extractor
from config.settings import (
    SONDA_QUERY_USER,
    SONDA_QUERY_PASSWORD, 
    RAW_CIRCUITOS_PATH,
    )

class Circuitos_Scraper(Extractor):
    """Download from the Circuitos source."""

    name = "Circuitos"

    # Constructor sub-method
    # Prepares the download directory 
    def __init__(self, config_path: Path | None = None) -> None:
        self.download_dir = RAW_CIRCUITOS_PATH
        self.download_dir.mkdir(parents=True, exist_ok=True)

    # Private sub-method
    # Instanciate Chrome Webdriver throught Selenium package
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
        #//*[@id="navbar-fixed-left"]/ul/li[1]/ul/li/ul/li[1]/ul/li[2]/a[1]
        menu_item = wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="navbar-fixed-left"]/ul/li[1]/ul/li/ul/li[1]/ul/li[2]/a[1]')))
        driver.save_screenshot(str(self.download_dir / "step8_menuitem_visible.png"))
        driver.execute_script("arguments[0].click();", menu_item)
        driver.save_screenshot(str(self.download_dir / "step9_menuitem_clicked.png"))
        # Wait for the report iframe to appear
        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)
        driver.save_screenshot(str(self.download_dir / "step10_Circuitos_iframe.png")) # <- Checkpoint

        # Un-comment only if _request_report_by_date() is deactivated in scrape() last method:
        #driver.switch_to.default_content()

    # Private sub-method
    # Sets a native input[type=date] value via JS in ISO format,
    # bypassing the browser locale and notifying AngularJS via events.
    def _set_date_input(self, driver: webdriver.Chrome, elem, iso_date: str) -> None:
        """iso_date must be in 'YYYY-MM-DD' format."""
        driver.execute_script("""
            const el = arguments[0];
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(el, arguments[1]);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        """, elem, iso_date)

        # Private sub-method
    # Waits for a new .csv to appear in download_dir, stabilizes its size,
    # renames it to target_name, and returns the final path.
    def _wait_and_rename_download(
        self,
        existing: set,
        target_name: str,
        timeout: int = 120,
    ) -> Path:
        interval = 1
        elapsed = 0
        new_file = None

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

        if new_file is None:
            raise TimeoutError(f"Download did not complete within {timeout} seconds.")

        # Size stabilization
        previous_size = -1
        while True:
            current_size = new_file.stat().st_size
            if current_size == previous_size and current_size > 0:
                break
            previous_size = current_size
            time.sleep(0.5)

        target = self.download_dir / target_name
        if target.exists():
            target.unlink()
        new_file.rename(target)
        return target

    # Request report verifying actual date
    def _download_reports(
            self, driver: webdriver.Chrome, i_date: str, f_date: str, name_date: str) -> tuple:
        wait = WebDriverWait(driver, 20)

        # Locate both native date inputs positionally (only 2 exist in this view)
        date_inputs = wait.until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='date']"))
        )
        if len(date_inputs) < 2:
            raise RuntimeError(f"Expected 2 date inputs, found {len(date_inputs)}")
        i_date_input, f_date_input = date_inputs[0], date_inputs[1]
        driver.save_screenshot(str(self.download_dir / "step11_dateboxes_localized.png"))

        # Set values via JS in ISO format (bypasses locale, fires AngularJS events)
        self._set_date_input(driver, i_date_input, i_date)
        self._set_date_input(driver, f_date_input, f_date)

        # Verify the values actually landed before proceeding
        actual_i = i_date_input.get_attribute("value")
        actual_f = f_date_input.get_attribute("value")
        if actual_i != i_date or actual_f != f_date:
            raise RuntimeError(
                f"Date inputs did not accept values. "
                f"Expected ({i_date}, {f_date}), got ({actual_i}, {actual_f})"
            )
        driver.save_screenshot(str(self.download_dir / "step12_dates_validated.png"))

        # Click on the Activo Checkbox
        btn_activo = wait.until(EC.element_to_be_clickable(
            (By.XPATH, '//*[@id="labelCheckbox"]')
            ))
        driver.execute_script("arguments[0].click();", btn_activo)
        driver.save_screenshot(str(self.download_dir / "step18_activo_clicked.png"))

        # --- Download 1: Reporte Desglosado ---
        existing_before_1 = set(self.download_dir.glob("*"))
        btn_desglosado = wait.until(EC.element_to_be_clickable(
            (By.XPATH,'//*[@id="root"]/div/main/section[1]/form/div[2]/button[1]')
        ))
        driver.execute_script("arguments[0].click();", btn_desglosado)
        driver.save_screenshot(str(self.download_dir / "step19_desglosado_clicked.png"))

        desglosado_path = self._wait_and_rename_download(
            existing=existing_before_1,
            target_name=f"Circ_desglosado_{name_date}.csv",
        )
        driver.save_screenshot(str(self.download_dir / "step20_desglosado_downloaded.png"))

        # --- Download 2: Reporte Ejecutivo ---
        existing_before_2 = set(self.download_dir.glob("*"))
        btn_ejecutivo = wait.until(EC.element_to_be_clickable(
            (By.XPATH, '//*[@id="root"]/div/main/section[1]/form/div[2]/button[2]')
        ))
        driver.execute_script("arguments[0].click();", btn_ejecutivo)
        driver.save_screenshot(str(self.download_dir / "step21_ejecutivo_clicked.png")) # <- Checkpoint

        ejecutivo_path = self._wait_and_rename_download(
            existing=existing_before_2,
            target_name=f"Circ_ejecutivo_{name_date}.csv",
        )
        driver.save_screenshot(str(self.download_dir / "step22_ejecutivo_downloaded.png"))

        return desglosado_path, ejecutivo_path

    
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
        """Scrape data from the Sonda website for the previous day."""
        monday, sunday = last_completed_week_cdmx()
        iso_date_i = monday.strftime("%Y-%m-%d")
        iso_date_f = sunday.strftime("%Y-%m-%d")
        name_date  = f"{monday.strftime('%d%m%y')}_{sunday.strftime('%d%m%y')}"

        driver = self._start_driver()
        try:
            self._login(driver)
            self._navigate_to_report(driver)
            #desglosado_path, ejecutivo_path = self._download_report_by_date_and_hour(driver, iso_date_i, iso_date_f, name_date)
            self._download_reports(driver, iso_date_i, iso_date_f, name_date)
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
# En prompt invocas python -m extract.scrapers.Circuitos
if __name__ == "__main__":
    scraper = Circuitos_Scraper()
    scraper.run()