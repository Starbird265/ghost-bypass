#!/usr/bin/env python3
"""
ghost_bypass.support.cookies
==============================
Per-domain cookie persistence.

Saves cookies from Playwright or Selenium to disk and reloads them on
the next visit. Using saved cookies lets you skip Cloudflare challenges
on repeat visits without solving them again.

Cookies auto-expire after *max_age_days* (default 7).
"""

import json
import time
import logging
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, List

logger = logging.getLogger(__name__)

DEFAULT_COOKIE_DIR = Path.home() / ".ghost_bypass" / "cookies"
DEFAULT_MAX_AGE_DAYS = 7


class CookieManager:
    """
    Save and load browser cookies per domain.

    Cookies are stored as JSON in *cookie_dir* (default
    ``~/.ghost_bypass/cookies/``).  Each domain gets its own file.

    Works with both Playwright and Selenium.
    """

    def __init__(
        self,
        cookie_dir: Optional[Path] = None,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
        ttl_days: Optional[int] = None,
    ):
        self.cookie_dir = Path(cookie_dir or DEFAULT_COOKIE_DIR)
        self.cookie_dir.mkdir(parents=True, exist_ok=True)
        # ttl_days is a friendlier alias for max_age_days
        self.max_age_days = ttl_days if ttl_days is not None else max_age_days

    # ── Playwright ────────────────────────────────────────────────────────

    def save_playwright(self, page, url: str) -> bool:
        """Save cookies from a Playwright page to disk."""
        try:
            domain = self._domain(url)
            cookies = page.context.cookies()
            self._write(domain, cookies)
            logger.debug("[Cookies] Saved %d cookies for %s", len(cookies), domain)
            return True
        except Exception as exc:
            logger.warning("[Cookies] Playwright save failed: %s", exc)
            return False

    def load_playwright(self, page, url: str) -> bool:
        """Load saved cookies into a Playwright page. Returns True if loaded."""
        try:
            domain = self._domain(url)
            cookies = self._read(domain)
            if not cookies:
                return False
            page.context.add_cookies(cookies)
            logger.debug("[Cookies] Loaded %d cookies for %s", len(cookies), domain)
            return True
        except Exception as exc:
            logger.warning("[Cookies] Playwright load failed: %s", exc)
            return False

    # ── Selenium ──────────────────────────────────────────────────────────

    def save_selenium(self, driver, url: str) -> bool:
        """Save cookies from a Selenium driver to disk."""
        try:
            domain = self._domain(url)
            cookies = driver.get_cookies()
            self._write(domain, cookies)
            logger.debug("[Cookies] Saved %d Selenium cookies for %s", len(cookies), domain)
            return True
        except Exception as exc:
            logger.warning("[Cookies] Selenium save failed: %s", exc)
            return False

    def load_selenium(self, driver, url: str) -> bool:
        """Load saved cookies into a Selenium driver. Returns True if loaded."""
        try:
            domain = self._domain(url)
            cookies = self._read(domain)
            if not cookies:
                return False
            current = driver.current_url
            if domain not in current:
                return False
            for c in cookies:
                clean = {
                    k: v for k, v in c.items()
                    if k in ("name", "value", "domain", "path", "expiry", "secure", "httpOnly")
                }
                try:
                    driver.add_cookie(clean)
                except Exception:
                    pass
            logger.debug("[Cookies] Loaded %d Selenium cookies for %s", len(cookies), domain)
            return True
        except Exception as exc:
            logger.warning("[Cookies] Selenium load failed: %s", exc)
            return False

    # ── Housekeeping ──────────────────────────────────────────────────────

    def clear(self, url: str):
        """Delete saved cookies for a specific domain."""
        path = self._path(self._domain(url))
        if path.exists():
            path.unlink()

    def clear_all(self):
        """Delete all saved cookie files."""
        for f in self.cookie_dir.glob("*.json"):
            f.unlink()
        logger.info("[Cookies] All cookies cleared")

    def list_domains(self) -> List[str]:
        """Return list of domains with saved cookies."""
        domains = []
        for f in self.cookie_dir.glob("*.json"):
            name = f.stem
            # Reverse the encoding: --DOT-- back to . and _ stays as _
            domain = name.replace("--DOT--", ".").replace("__SLASH__", "/")
            domains.append(domain)
        return domains

    # ── Private ───────────────────────────────────────────────────────────

    def _path(self, domain: str) -> Path:
        # Use --DOT-- for dots to avoid collision with multi-part TLDs
        # e.g. example.co.uk -> example--DOT--co--DOT--uk.json
        safe = domain.replace(".", "--DOT--").replace("/", "__SLASH__")
        return self.cookie_dir / f"{safe}.json"

    def _domain(self, url: str) -> str:
        try:
            netloc = urlparse(url).netloc.lower()
            return netloc.removeprefix("www.")
        except Exception:
            return "unknown"

    def _write(self, domain: str, cookies: list):
        data = {"saved_at": time.time(), "domain": domain, "cookies": cookies}
        with open(self._path(domain), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def _read(self, domain: str) -> list:
        path = self._path(domain)
        if not path.exists():
            # Backwards compat: try the old underscore-based filename
            old_safe = domain.replace(".", "_").replace("/", "_")
            old_path = self.cookie_dir / f"{old_safe}.json"
            if old_path.exists():
                path = old_path
            else:
                return []
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            age = (time.time() - data.get("saved_at", 0)) / 86400
            if age > self.max_age_days:
                path.unlink()
                return []
            return data.get("cookies", [])
        except Exception as exc:
            logger.warning("[Cookies] Read error for %s: %s", domain, exc)
            return []

    # ── Bulk export/import ─────────────────────────────────────────────────

    def export_all(self, path: str):
        """Export all saved cookies to a single JSON file."""
        all_cookies = {}
        for f in self.cookie_dir.glob("*.json"):
            try:
                with open(f, encoding="utf-8") as fh:
                    all_cookies[f.stem] = json.load(fh)
            except Exception:
                pass
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(all_cookies, fh, indent=2, default=str)
        logger.info("[Cookies] Exported %d domains to %s", len(all_cookies), path)

    def import_all(self, path: str):
        """Import cookies from a bulk export file."""
        with open(path, encoding="utf-8") as fh:
            all_cookies = json.load(fh)
        for stem, data in all_cookies.items():
            out_path = self.cookie_dir / f"{stem}.json"
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, default=str)
        logger.info("[Cookies] Imported %d domains from %s", len(all_cookies), path)
