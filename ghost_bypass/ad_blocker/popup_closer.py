#!/usr/bin/env python3
"""
ghost_bypass.ad_blocker.popup_closer
=====================================
Thread-safe popup window monitor + in-browser JS interval overlay killer.

Runs a background thread that checks for and closes popup windows every
*interval* seconds, and simultaneously injects a JavaScript setInterval
that removes overlay elements every 500 ms from inside the browser.

Works on **any** website. Uses Selenium WebDriver.
"""

import time
import logging
import threading
from typing import Optional

try:
    from ghost_bypass.support.human import HumanBehavior
    _HUMAN_AVAILABLE = True
except ImportError:
    _HUMAN_AVAILABLE = False

logger = logging.getLogger(__name__)


class PopupCloser:
    """
    Thread-safe popup / overlay handler for Selenium.

    Usage (blocking mode)::

        closer = PopupCloser()
        closer.close_all(driver, original_url)

    Usage (background monitor mode)::

        lock   = threading.Lock()
        closer = PopupCloser()
        closer.start_monitoring(driver, lock, interval=2.0)
        # ... do your scraping ...
        closer.stop_monitoring()
    """

    CLOSE_SELECTORS = [
        "button.close", "a.close", "span.close", "div.close",
        "[class*='close']", "[class*='Close']",
        "[id*='close']", "[aria-label*='close' i]", "[aria-label*='dismiss' i]",
        "button[class*='dismiss']", ".modal-close", ".popup-close",
        ".overlay-close", ".fancybox-close", ".mfp-close", ".pum-close",
        "#onetrust-accept-btn-handler", ".cc-dismiss",
        "[data-testid='close-button']",
        "button[style*='position: absolute'][style*='right']",
        "span[style*='position: absolute'][style*='right']",
    ]

    def __init__(self, max_attempts: int = 5, wait_seconds: float = 1.5):
        self.max_attempts = max_attempts
        self.wait_seconds = wait_seconds
        self.stats = {"popups": 0, "overlays": 0, "attempts": 0}
        self.human = HumanBehavior() if _HUMAN_AVAILABLE else None
        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None

    # ── Blocking API ──────────────────────────────────────────────────────

    def close_all(self, driver, original_url: str) -> dict:
        """Close all popup windows and overlays. Returns stats dict."""
        self.stats = {"popups": 0, "overlays": 0, "attempts": 0}
        for attempt in range(self.max_attempts):
            self.stats["attempts"] = attempt + 1
            n_pop = self._close_popup_windows(driver)
            self.stats["popups"] += n_pop
            time.sleep(0.4)
            n_ov = self._click_close_buttons(driver)
            self.stats["overlays"] += n_ov

            try:
                if not self._same_page(driver.current_url, original_url):
                    driver.get(original_url)
                    time.sleep(2)
            except Exception:
                pass

            if n_pop == 0 and n_ov == 0:
                break
            if attempt < self.max_attempts - 1:
                time.sleep(self.wait_seconds)

        logger.info(
            "[PopupCloser] %d windows + %d overlays closed",
            self.stats["popups"], self.stats["overlays"],
        )
        return self.stats

    # ── Background monitor API ────────────────────────────────────────────

    def start_monitoring(self, driver, lock: threading.Lock, interval: float = 2.0):
        """Start a background thread that closes popup windows periodically."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event = threading.Event()

        def _loop():
            logger.debug("[PopupCloser] Monitor thread started")
            while not self._stop_event.is_set():
                try:
                    if lock.acquire(timeout=1.0):
                        try:
                            self._close_popup_windows(driver)
                        finally:
                            lock.release()
                except Exception as exc:
                    logger.debug("[PopupCloser] Monitor error: %s", exc)
                time.sleep(interval)
            logger.debug("[PopupCloser] Monitor thread stopped")

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()
        self.inject_js_monitor(driver)

    def stop_monitoring(self):
        """Stop the background monitor thread."""
        if self._stop_event:
            self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    # ── JS monitor injection ──────────────────────────────────────────────

    def inject_js_monitor(self, driver) -> bool:
        """
        Inject a JavaScript setInterval that removes overlay elements every
        500 ms from within the browser engine itself.
        """
        js = """
        if (window._ghostBypassMonitor) clearInterval(window._ghostBypassMonitor);
        window._ghostBypassMonitor = setInterval(() => {
            document.querySelectorAll(
                'div[style*="z-index"], div[class*="overlay"], ' +
                'div[class*="popup"], div[class*="modal"], ' +
                'div[class*="cookie"], div[class*="consent"], ' +
                'iframe[style*="position: fixed"]'
            ).forEach(el => {
                const s = window.getComputedStyle(el);
                const z = parseInt(s.zIndex) || 0;
                if ((z > 50 && (s.position === 'fixed' || s.position === 'absolute') && s.opacity > 0) ||
                    /popup|overlay|modal|cookie|consent/i.test(el.id + el.className)) {
                    if (!['HEADER','NAV'].includes(el.tagName) &&
                        !/header|navbar/i.test(el.id + el.className)) {
                        el.remove();
                    }
                }
            });
            document.body.style.overflow = 'auto';
            document.documentElement.style.overflow = 'auto';
        }, 500);
        """
        try:
            driver.execute_script(js)
            logger.debug("[PopupCloser] JS monitor injected")
            return True
        except Exception as exc:
            logger.debug("[PopupCloser] JS inject failed: %s", exc)
            return False

    # ── Internal helpers ──────────────────────────────────────────────────

    def _close_popup_windows(self, driver) -> int:
        closed = 0
        try:
            main = driver.current_window_handle
            for w in driver.window_handles:
                if w != main:
                    try:
                        driver.switch_to.window(w)
                        driver.close()
                        closed += 1
                    except Exception:
                        pass
            driver.switch_to.window(main)
        except Exception:
            pass
        return closed

    def _click_close_buttons(self, driver) -> int:
        try:
            from selenium.webdriver.common.by import By
            from selenium.common.exceptions import (
                NoSuchElementException, ElementClickInterceptedException
            )
        except ImportError:
            return 0

        closed = 0
        pairs = [(By.CSS_SELECTOR, s) for s in self.CLOSE_SELECTORS] + [
            (By.XPATH, '//*[contains(text(), "✕")]'),
            (By.XPATH, '//*[contains(text(), "×")]'),
            (By.XPATH, '//*[contains(text(), "Accept")]'),
            (By.XPATH, '//*[contains(text(), "Got it")]'),
            (By.XPATH, '//*[contains(text(), "Dismiss")]'),
        ]
        for by, sel in pairs:
            try:
                for el in driver.find_elements(by, sel):
                    try:
                        if el.is_displayed() and el.is_enabled():
                            if self.human:
                                self.human.human_click(driver, el)
                            else:
                                el.click()
                                time.sleep(0.3)
                            closed += 1
                    except (NoSuchElementException, ElementClickInterceptedException):
                        continue
            except Exception:
                continue
        return closed

    @staticmethod
    def _same_page(url1: str, url2: str) -> bool:
        from urllib.parse import urlparse
        p1, p2 = urlparse(url1), urlparse(url2)
        return p1.scheme == p2.scheme and p1.netloc == p2.netloc and p1.path == p2.path

    def get_stats(self) -> dict:
        return self.stats.copy()

    def reset_stats(self):
        self.stats = {"popups": 0, "overlays": 0, "attempts": 0}
