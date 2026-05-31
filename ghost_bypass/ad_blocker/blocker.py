#!/usr/bin/env python3
"""
ghost_bypass.ad_blocker.blocker
================================
General-purpose ad overlay & popup handler.

Works on ANY website — closes floating overlays, modal dialogs, GDPR banners,
cookie consent notices, newsletter popups, and any other element that blocks
page content.

Supports Playwright and Selenium WebDriver.
"""

import time
import logging
from typing import List

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Page = None

try:
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import (
        NoSuchElementException,
        ElementClickInterceptedException,
    )
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    By = None
    NoSuchElementException = Exception
    ElementClickInterceptedException = Exception


class AdBlocker:
    """
    General-purpose ad / popup / overlay handler.

    Supports:
    - Floating ad overlays with X / close buttons
    - Cookie consent banners (GDPR)
    - Newsletter signup modals
    - Full-page interstitial ads
    - Redirect-loop recovery (navigates back to *initial_url* if redirected)
    - Direct DOM removal as a last resort

    Works on any website, not tied to any content type.
    """

    # ── Common close-button selectors ─────────────────────────────────────
    CLOSE_BUTTON_SELECTORS: List[str] = [
        # Standard class names
        "button.close", "a.close", "div.close", "span.close",
        "[class*='close']", "[id*='close']",
        "[class*='Close']", "[id*='Close']",
        "[class*='dismiss']", "[id*='dismiss']",
        "[class*='Dismiss']",

        # ARIA / title attributes
        "[aria-label*='close' i]", "[aria-label*='dismiss' i]",
        "[title*='close' i]",

        # Named CSS patterns
        ".fancybox-close", ".mfp-close", ".modal-close",
        ".popup-close", ".overlay-close", ".lightbox-close",
        ".pum-close",                         # Popup Maker
        ".cookie-close", ".gdpr-close",       # Cookie banners

        # GDPR / consent frameworks
        "#onetrust-accept-btn-handler",
        ".cc-dismiss", ".cc-btn",
        "[data-testid='close-button']",

        # onclick-based
        "[onclick*='close']", "[onclick*='Close']", "[onclick*='hide']",

        # Position-based (small X in corner)
        "button[style*='position: absolute'][style*='right']",
        "span[style*='position: absolute'][style*='right']",

        # Unicode X characters (Playwright :has-text not valid in Selenium)
        "button:has-text('✕')", "button:has-text('×')", "button:has-text('✖')",
        "a:has-text('✕')", "span:has-text('×')",
    ]

    # ── Ad overlay container selectors ────────────────────────────────────
    OVERLAY_SELECTORS: List[str] = [
        "div[class*='overlay']", "div[class*='popup']", "div[class*='modal']",
        "div[id*='overlay']", "div[id*='popup']", "div[id*='modal']",
        "div[style*='z-index: 999']", "div[style*='z-index: 9999']",
        "div[style*='position: fixed']",
        "div[class*='cookie']", "div[id*='cookie']",
        "div[class*='gdpr']", "div[id*='gdpr']",
        "div[class*='consent']", "div[id*='consent']",
        "iframe[src*='ad']", "iframe[id*='ad']",
    ]

    def __init__(self, max_iterations: int = 5, wait_between: float = 1.5):
        """
        Parameters
        ----------
        max_iterations:
            Maximum close-attempt cycles per call.
        wait_between:
            Seconds to wait between iterations.
        """
        self.max_iterations = max_iterations
        self.wait_between = wait_between
        self.stats = {"ads_closed": 0, "popups_closed": 0, "iterations": 0}

    # ── Public API ────────────────────────────────────────────────────────

    def handle_playwright(self, page: "Page", initial_url: str) -> bool:
        """Close overlays on a Playwright page. Returns True when finished."""
        if not PLAYWRIGHT_AVAILABLE:
            logger.warning("Playwright not installed.")
            return False

        logger.info("🚫 [AdBlocker] Playwright overlay pass starting…")
        for i in range(self.max_iterations):
            self.stats["iterations"] = i + 1

            closed_tabs = self._close_extra_tabs_playwright(page)
            self.stats["popups_closed"] += closed_tabs

            page.wait_for_timeout(400)

            closed_overlays = self._click_close_buttons_playwright(page)
            self.stats["ads_closed"] += closed_overlays

            if closed_overlays:
                page.wait_for_timeout(int(self.wait_between * 1000))
                if not self._same_page(page.url, initial_url):
                    logger.warning("↩️ Redirected — navigating back")
                    try:
                        page.goto(initial_url, wait_until="domcontentloaded", timeout=10_000)
                    except Exception:
                        pass
            else:
                break


        logger.info(
            "🎯 [AdBlocker] Done — %d overlays + %d tabs closed",
            self.stats["ads_closed"],
            self.stats["popups_closed"],
        )
        return True

    def handle_selenium(self, driver, initial_url: str) -> bool:
        """Close overlays on a Selenium WebDriver. Returns True when finished."""
        logger.info("🚫 [AdBlocker] Selenium overlay pass starting…")
        for i in range(self.max_iterations):
            self.stats["iterations"] = i + 1

            closed_tabs = self._close_extra_tabs_selenium(driver)
            self.stats["popups_closed"] += closed_tabs

            time.sleep(0.4)

            closed_overlays = self._click_close_buttons_selenium(driver)
            self.stats["ads_closed"] += closed_overlays

            if closed_overlays:
                time.sleep(self.wait_between)
                if not self._same_page(driver.current_url, initial_url):
                    try:
                        driver.get(initial_url)
                        time.sleep(2)
                    except Exception:
                        pass
            else:
                break

        logger.info(
            "🎯 [AdBlocker] Done — %d overlays + %d tabs closed",
            self.stats["ads_closed"],
            self.stats["popups_closed"],
        )
        return True

    # ── Playwright internals ──────────────────────────────────────────────

    def _click_close_buttons_playwright(self, page) -> int:
        closed = 0
        for sel in self.CLOSE_BUTTON_SELECTORS:
            try:
                buttons = page.locator(sel).all()
                for btn in buttons:
                    try:
                        if btn.is_visible(timeout=400):
                            btn.scroll_into_view_if_needed(timeout=800)
                            btn.click(timeout=1500, force=True)
                            closed += 1
                            page.wait_for_timeout(400)
                    except Exception:
                        continue
            except Exception:
                continue

        if closed == 0:
            closed += self._remove_overlays_js_playwright(page)
        return closed

    def _remove_overlays_js_playwright(self, page) -> int:
        try:
            removed = page.evaluate(
                """() => {
                    let n = 0;
                    document.querySelectorAll(
                        'div[style*="z-index"], div[class*="overlay"], ' +
                        'div[class*="popup"], div[class*="modal"], ' +
                        'div[class*="cookie"], div[class*="consent"]'
                    ).forEach(el => {
                        const s = window.getComputedStyle(el);
                        if ((parseInt(s.zIndex) > 100 &&
                             (s.position === 'fixed' || s.position === 'absolute')) ||
                            /overlay|popup|modal|cookie|consent/i.test(el.className)) {
                            el.remove(); n++;
                        }
                    });
                    document.body.style.overflow = 'auto';
                    document.documentElement.style.overflow = 'auto';
                    return n;
                }"""
            )
            if removed:
                logger.debug("🗑️ Removed %d overlays via JS (Playwright)", removed)
            return removed or 0
        except Exception:
            return 0

    def _close_extra_tabs_playwright(self, page) -> int:
        closed = 0
        try:
            for p in page.context.pages:
                if p != page:
                    try:
                        p.close()
                        closed += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return closed

    # ── Selenium internals ────────────────────────────────────────────────

    def _click_close_buttons_selenium(self, driver) -> int:
        if not SELENIUM_AVAILABLE:
            return 0
        closed = 0
        css_selectors = [
            "button.close", "a.close", "span.close", "div.close",
            "[class*='close']", "[id*='close']", "[class*='Close']",
            "[class*='dismiss']", "[aria-label*='close']",
            ".modal-close", ".popup-close", ".overlay-close",
            ".fancybox-close", ".mfp-close", ".pum-close",
            "#onetrust-accept-btn-handler", ".cc-dismiss",
            "[data-testid='close-button']",
        ]
        pairs = [(By.CSS_SELECTOR, s) for s in css_selectors] + [
            (By.XPATH, '//*[contains(text(), "✕")]'),
            (By.XPATH, '//*[contains(text(), "×")]'),
            (By.XPATH, '//*[contains(text(), "✖")]'),
            (By.XPATH, '//*[contains(text(), "Accept")]'),
            (By.XPATH, '//*[contains(text(), "Got it")]'),
        ]
        for by, sel in pairs:
            try:
                for el in driver.find_elements(by, sel):
                    try:
                        if el.is_displayed() and el.is_enabled():
                            el.click()
                            closed += 1
                            time.sleep(0.3)
                    except (NoSuchElementException, ElementClickInterceptedException):
                        continue
            except Exception:
                continue
        if closed == 0:
            closed += self._remove_overlays_js_selenium(driver)
        return closed

    def _remove_overlays_js_selenium(self, driver) -> int:
        try:
            removed = driver.execute_script("""
                let n = 0;
                document.querySelectorAll(
                    'div[style*="z-index"], div[class*="overlay"], ' +
                    'div[class*="popup"], div[class*="modal"], ' +
                    'div[class*="cookie"], div[class*="consent"]'
                ).forEach(el => {
                    const s = window.getComputedStyle(el);
                    if ((parseInt(s.zIndex) > 100 &&
                         (s.position === 'fixed' || s.position === 'absolute')) ||
                        /overlay|popup|modal|cookie|consent/i.test(el.className)) {
                        el.remove(); n++;
                    }
                });
                document.body.style.overflow = 'auto';
                document.documentElement.style.overflow = 'auto';
                return n;
            """)
            return removed or 0
        except Exception:
            return 0

    def _close_extra_tabs_selenium(self, driver) -> int:
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

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _same_page(url1: str, url2: str) -> bool:
        from urllib.parse import urlparse
        p1, p2 = urlparse(url1), urlparse(url2)
        return p1.scheme == p2.scheme and p1.netloc == p2.netloc and p1.path == p2.path

    def get_stats(self) -> dict:
        return self.stats.copy()

    def reset_stats(self):
        self.stats = {"ads_closed": 0, "popups_closed": 0, "iterations": 0}
