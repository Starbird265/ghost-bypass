#!/usr/bin/env python3
"""
ghost_bypass.cloudflare.handler
================================
Detects Cloudflare challenge / DDOS-Guard / WAF interstitial pages and
waits for them to resolve automatically or with human-like interaction.

Also detects Akamai Bot Manager and DataDome WAFs.

Supports both Playwright pages and Selenium WebDriver instances.
"""

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Detection phrases ──────────────────────────────────────────────────────
_CF_TITLE_PHRASES = [
    "just a moment",
    "checking your browser",
    "please wait",
    "ddos-guard",
    "attention required",
    "cloudflare",
    "ray id",
    "security check",
    "one more step",
    "verifying you are human",
    "enable javascript and cookies",
]

_CF_BODY_PHRASES = [
    "cf-browser-verification",
    "cf_clearance",
    "challenge-platform",
    "__cf_bm",
    "cloudflare ray id",
    "enable javascript",
    "checking if the site connection is secure",
    "this process is automatic",
    "ddos-guard.net",
    "cf-turnstile",
]

# ── Akamai Bot Manager detection ──────────────────────────────────────────
_AKAMAI_PHRASES = [
    "akamai",
    "_abck",
    "ak_bmsc",
    "bot manager",
    "access denied",
    "reference #",
]

# ── DataDome detection ────────────────────────────────────────────────────
_DATADOME_PHRASES = [
    "datadome",
    "dd_s",
    "geo.captcha-delivery.com",
    "captcha-delivery.com",
    "interstitialcaptcha",
]


class CloudflareHandler:
    """
    Static utility class for detecting and waiting out Cloudflare / WAF
    challenges.

    Works with **any** website — not limited to any content type.
    """

    # ── Playwright ─────────────────────────────────────────────────────────

    @staticmethod
    def is_challenge_page_playwright(page) -> bool:
        """Return True if the current Playwright page is a CF/WAF challenge."""
        try:
            title = (page.title() or "").lower()
            body = page.content().lower()

            # Standard CF check
            if (
                any(p in title for p in _CF_TITLE_PHRASES)
                or any(p in body for p in _CF_BODY_PHRASES)
            ):
                return True

            # Turnstile iframe check
            try:
                iframes = page.locator("iframe").all()
                for iframe in iframes:
                    src = iframe.get_attribute("src") or ""
                    if "challenges.cloudflare.com" in src or "cf-turnstile" in src:
                        return True
            except Exception:
                pass

            return False
        except Exception as exc:
            logger.debug("[CF] Playwright check error: %s", exc)
            return False

    @staticmethod
    def is_akamai_page_playwright(page) -> bool:
        """Return True if the page is blocked by Akamai Bot Manager."""
        try:
            body = page.content().lower()
            return sum(1 for p in _AKAMAI_PHRASES if p in body) >= 2
        except Exception:
            return False

    @staticmethod
    def is_datadome_page_playwright(page) -> bool:
        """Return True if the page is blocked by DataDome."""
        try:
            body = page.content().lower()
            return sum(1 for p in _DATADOME_PHRASES if p in body) >= 2
        except Exception:
            return False

    @staticmethod
    def is_any_waf_playwright(page) -> bool:
        """Return True if any WAF (CF, Akamai, DataDome) is detected."""
        return (
            CloudflareHandler.is_challenge_page_playwright(page)
            or CloudflareHandler.is_akamai_page_playwright(page)
            or CloudflareHandler.is_datadome_page_playwright(page)
        )

    @staticmethod
    def wait_for_challenge_resolution_playwright(page, timeout: int = 60) -> bool:
        """
        Wait for CF challenge to resolve using cookie polling (faster than
        fixed sleep intervals).

        Returns True on success, False on timeout.
        """
        logger.info("[CF] Waiting up to %ds for challenge to resolve…", timeout)
        end = time.time() + timeout

        # First, try the fast path: poll for cf_clearance cookie via JS
        try:
            page.evaluate("""() => {
                window.__ghostCFResolved = false;
                const check = setInterval(() => {
                    if (document.cookie.includes('cf_clearance')) {
                        window.__ghostCFResolved = true;
                        clearInterval(check);
                    }
                }, 300);
                setTimeout(() => clearInterval(check), %d);
            }""" % (timeout * 1000))
        except Exception:
            pass

        while time.time() < end:
            time.sleep(1.5)
            try:
                # Check if JS cookie monitor detected clearance
                try:
                    resolved = page.evaluate("() => window.__ghostCFResolved === true")
                    if resolved:
                        logger.info("[CF] ✅ Challenge resolved (cookie detected)!")
                        return True
                except Exception:
                    pass

                # Fallback: check if we're still on challenge page
                if not CloudflareHandler.is_challenge_page_playwright(page):
                    logger.info("[CF] ✅ Challenge resolved!")
                    return True
                elapsed = int(time.time() - (end - timeout))
                logger.debug("[CF] Still on challenge page… %ds elapsed", elapsed)
            except Exception:
                break
        logger.warning("[CF] ⏰ Challenge timed out")
        return False

    # ── Selenium ───────────────────────────────────────────────────────────

    @staticmethod
    def is_challenge_page_selenium(driver) -> bool:
        """Return True if the Selenium driver is on a CF/WAF challenge page."""
        try:
            title = (driver.title or "").lower()
            if any(p in title for p in _CF_TITLE_PHRASES):
                return True
            try:
                has_cf = driver.execute_script(
                    "return document.cookie.includes('cf_clearance') || "
                    "document.body.innerText.toLowerCase().includes('checking your browser');"
                )
                if has_cf:
                    body = driver.page_source.lower()
                    return any(p in body for p in _CF_BODY_PHRASES)
            except Exception:
                pass

            # Turnstile iframe check (Selenium)
            try:
                from selenium.webdriver.common.by import By
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                for iframe in iframes:
                    src = iframe.get_attribute("src") or ""
                    if "challenges.cloudflare.com" in src or "cf-turnstile" in src:
                        return True
            except Exception:
                pass

            return False
        except Exception as exc:
            logger.debug("[CF] Selenium check error: %s", exc)
            return False

    @staticmethod
    def is_akamai_page_selenium(driver) -> bool:
        """Return True if the page is blocked by Akamai Bot Manager."""
        try:
            body = driver.page_source.lower()
            return sum(1 for p in _AKAMAI_PHRASES if p in body) >= 2
        except Exception:
            return False

    @staticmethod
    def is_datadome_page_selenium(driver) -> bool:
        """Return True if the page is blocked by DataDome."""
        try:
            body = driver.page_source.lower()
            return sum(1 for p in _DATADOME_PHRASES if p in body) >= 2
        except Exception:
            return False

    @staticmethod
    def wait_for_challenge_resolution_selenium(driver, timeout: int = 90) -> bool:
        """
        Poll the Selenium driver until the challenge resolves or *timeout*
        seconds pass.

        Returns True on success, False on timeout.
        """
        logger.info("[CF] Waiting up to %ds for challenge (Selenium)…", timeout)
        end = time.time() + timeout

        # Inject JS cookie monitor
        try:
            driver.execute_script("""
                window.__ghostCFResolved = false;
                var check = setInterval(function() {
                    if (document.cookie.indexOf('cf_clearance') !== -1) {
                        window.__ghostCFResolved = true;
                        clearInterval(check);
                    }
                }, 300);
                setTimeout(function() { clearInterval(check); }, %d);
            """ % (timeout * 1000))
        except Exception:
            pass

        while time.time() < end:
            time.sleep(1.5)
            try:
                # Fast path: JS cookie monitor
                try:
                    resolved = driver.execute_script(
                        "return window.__ghostCFResolved === true"
                    )
                    if resolved:
                        logger.info("[CF] ✅ Challenge resolved (cookie detected, Selenium)!")
                        return True
                except Exception:
                    pass

                if not CloudflareHandler.is_challenge_page_selenium(driver):
                    logger.info("[CF] ✅ Challenge resolved (Selenium)!")
                    return True
            except Exception:
                break
        logger.warning("[CF] ⏰ Challenge timed out (Selenium)")
        return False

    # ── WAF type identification ────────────────────────────────────────────

    @staticmethod
    def identify_waf_playwright(page) -> Optional[str]:
        """Identify which WAF is blocking the page. Returns name or None."""
        if CloudflareHandler.is_challenge_page_playwright(page):
            return "cloudflare"
        if CloudflareHandler.is_akamai_page_playwright(page):
            return "akamai"
        if CloudflareHandler.is_datadome_page_playwright(page):
            return "datadome"
        return None

    @staticmethod
    def identify_waf_selenium(driver) -> Optional[str]:
        """Identify which WAF is blocking the page. Returns name or None."""
        if CloudflareHandler.is_challenge_page_selenium(driver):
            return "cloudflare"
        if CloudflareHandler.is_akamai_page_selenium(driver):
            return "akamai"
        if CloudflareHandler.is_datadome_page_selenium(driver):
            return "datadome"
        return None

    # ── Diagnostics ────────────────────────────────────────────────────────

    @staticmethod
    def diagnostic_playwright(page) -> dict:
        """Return diagnostic dict for a blocked Playwright page."""
        try:
            waf = CloudflareHandler.identify_waf_playwright(page)
            return {"url": page.url, "title": page.title(), "waf": waf}
        except Exception:
            return {}

    @staticmethod
    def diagnostic_selenium(driver) -> dict:
        """Return diagnostic dict for a blocked Selenium page."""
        try:
            waf = CloudflareHandler.identify_waf_selenium(driver)
            return {"url": driver.current_url, "title": driver.title, "waf": waf}
        except Exception:
            return {}
