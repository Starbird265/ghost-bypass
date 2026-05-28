#!/usr/bin/env python3
"""
ghost_bypass.engine.engine
============================
BypassEngine — general-purpose, ML-guided web scraping engine.

Escalation levels (L0 → L11)
------------------------------
  L0  requests_basic          — plain requests, real headers, fast
  L1  requests_tls            — curl_cffi TLS fingerprint mimicry
  L2  httpx_http2             — HTTP/2 via httpx
  L3  playwright_stealth      — headless Chromium + stealth JS
  L4  playwright_headful      — visible Chromium + stealth JS
  L5  playwright_mobile_hl    — mobile emulation, headless
  L6  playwright_mobile_hf    — mobile emulation, headful
  L7  uc_headless             — undetected-chromedriver, headless
  L8  uc_headful              — undetected-chromedriver, headful (CF turnstile)
  L9  drission                — DrissionPage (Chromium hybrid)
  L10 requests_html           — pyppeteer JS render
  L11 mechanize               — classic HTTP (legacy sites)

ML brains
---------
  SiteLearner   — remembers which level works per domain; builds smart chain
  MLProxyManager— knows which proxy works best per domain; rotates on failure

Return value (``scrape()``)
---------------------------
  {
    'success'    : bool,
    'url'        : str,          # final URL after redirects
    'status_code': int | None,
    'html'       : str,          # full page HTML
    'text'       : str,          # plain text (stripped HTML)
    'title'      : str,
    'meta'       : dict,         # {name: content} meta tags
    'links'      : list[str],    # absolute href links
    'images'     : list[str],    # absolute img src URLs
    'scripts'    : list[str],    # absolute script src URLs
    'cookies'    : dict,
    'headers'    : dict,
    'method'     : str,          # e.g. "L3:playwright_stealth"
    'level'      : int,          # 0–11
    'cf_detected': bool,
    'duration'   : float,        # seconds
    'attempts'   : list[dict],   # per-attempt log
    'data'       : Any,          # custom extractor output (if provided)
    'error'      : str | None,
  }
"""

from __future__ import annotations

import logging
import random
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)


class MissingExtrasError(ImportError):
    """Raised when a scrape requires extras that are not installed.

    Provides a clear install hint instead of silently failing or
    returning a cryptic ImportError.
    """

    def __init__(self, needed: str, install_cmd: str, detail: str = ""):
        self.needed = needed
        self.install_cmd = install_cmd
        msg = (
            f"\n\n🚫  ghost_bypass: {needed} is required but not installed.\n"
            f"    Install it with:\n\n        {install_cmd}\n"
        )
        if detail:
            msg += f"\n    Context: {detail}\n"
        super().__init__(msg)

# ── Graceful optional imports ──────────────────────────────────────────────
def _try_import(module: str):
    try:
        import importlib
        return importlib.import_module(module)
    except ImportError:
        return None

# ── Level registry ────────────────────────────────────────────────────────
LEVELS: List[str] = [
    "L0_requests_basic",
    "L1_requests_tls",
    "L2_httpx_http2",
    "L3_playwright_stealth",
    "L4_playwright_headful",
    "L5_playwright_mobile_headless",
    "L6_playwright_mobile_headful",
    "L7_uc_headless",
    "L8_uc_headful",
    "L9_drission",
    "L10_requests_html",
    "L11_mechanize",
]

LEVEL_INDEX: Dict[str, int] = {name: i for i, name in enumerate(LEVELS)}

# Levels that cannot handle CF challenges — skip when CF is known
CF_INCAPABLE = {
    "L0_requests_basic", "L1_requests_tls", "L2_httpx_http2",
    "L3_playwright_stealth", "L5_playwright_mobile_headless",
    "L10_requests_html", "L11_mechanize",
}


class BypassEngine:
    """
    General-purpose ML-guided scraping engine.

    Parameters
    ----------
    proxy_manager:
        ``MLProxyManager`` instance (optional). If provided, proxies are
        automatically selected and rotated per domain.
    site_learner:
        ``SiteLearner`` instance (optional). If provided, per-domain method
        memory is used to choose the best starting level.
    ad_blocker:
        ``AdBlocker`` instance (optional).
    popup_closer:
        ``PopupCloser`` instance (optional).
    cookie_manager:
        ``CookieManager`` instance (optional).
    max_levels:
        Maximum number of levels to try before giving up.
    headful_on_cf:
        Automatically promote to L8 (uc_headful) if CF is detected.
    request_timeout:
        Default timeout for all HTTP requests (seconds).
    """

    def __init__(
        self,
        proxy_manager=None,
        site_learner=None,
        ad_blocker=None,
        popup_closer=None,
        cookie_manager=None,
        max_levels: int = len(LEVELS),
        headful_on_cf: bool = True,
        request_timeout: int = 30,
    ):
        self.proxy_manager = proxy_manager
        self.site_learner = site_learner
        self.ad_blocker = ad_blocker
        self.popup_closer = popup_closer
        self.cookie_manager = cookie_manager
        self.max_levels = max_levels
        self.headful_on_cf = headful_on_cf
        self.request_timeout = request_timeout

    # ── Primary API ───────────────────────────────────────────────────────

    def scrape(
        self,
        url: str,
        extractor: Optional[Callable[[str, str], Any]] = None,
        extract: Optional[Dict[str, str]] = None,
        prompt: Optional[str] = None,
        max_attempts: int = 2,
        proxy_attempts_per_level: int = 2,
        level_timeout: Optional[int] = None,
        lease_proxies: bool = True,
    ) -> dict:
        """
        Scrape *url* using the ML-guided level chain.

        For each level:
          - Try with the best proxy for this domain.
          - On proxy failure → try next-best proxy (up to *proxy_attempts_per_level*).
          - On level failure → advance to next level.
          - If CF detected → jump straight to headful UC mode.

        Parameters
        ----------
        url:
            Target URL.
        extractor:
            Optional ``fn(html: str, url: str) -> Any``.  Its return value
            is placed in ``result['data']``.  (Tier 2 extraction)
        extract:
            Optional dict of ``{"key": "css_or_xpath_selector"}`` for
            automatic data extraction.  (Tier 1 extraction)
        prompt:
            Optional natural-language prompt for AI extraction.  Requires
            the ``ai`` extra.  (Tier 3 extraction)
        max_attempts:
            Retries per level before escalating.
        proxy_attempts_per_level:
            How many different proxies to try per level.
        level_timeout:
            Per-level timeout cap in seconds. If a level exceeds this,
            it is abandoned. Defaults to ``request_timeout * 2``.

        Returns
        -------
        dict
            Rich result dict (see module docstring).
        """
        domain = _domain(url)
        result = _empty_result(url)
        attempts_log: List[dict] = []
        per_level_timeout = level_timeout or (self.request_timeout * 2)

        # Build level chain from ML memory — use a deque for safe mutation
        chain = deque(self._build_chain(domain)[:self.max_levels])

        cf_detected = False

        while chain:
            level_name = chain.popleft()
            level_int = LEVEL_INDEX.get(level_name, 99)
            method_fn = _METHOD_DISPATCH.get(level_name)
            if method_fn is None:
                continue

            # If CF confirmed AND this level can't handle it → skip
            if cf_detected and level_name in CF_INCAPABLE:
                logger.info("⏩ Skipping %s (CF-incapable, CF detected)", level_name)
                continue

            # We iterate over up to `proxy_attempts_per_level` proxies, plus a fallback to direct.
            level_succeeded = False
            proxies_tried = set()

            for proxy_idx in range(proxy_attempts_per_level + 1):
                if level_succeeded:
                    break
                
                proxy = None
                if self.proxy_manager and proxy_idx < proxy_attempts_per_level:
                    proxy = self.proxy_manager.get_best_proxy(
                        domain=domain, 
                        exclude=proxies_tried, 
                        lease=lease_proxies
                    )
                    if proxy:
                        proxies_tried.add(proxy)
                    else:
                        if None in proxies_tried:
                            break
                        proxies_tried.add(None)
                elif proxy_idx == proxy_attempts_per_level:
                    if None in proxies_tried:
                        break
                    proxy = None
                    proxies_tried.add(None)

                try:
                    for attempt in range(max_attempts):
                        label = f"{level_name} proxy={proxy or 'direct'} attempt={attempt + 1}"
                        logger.info("🔄 Trying %s", label)
                        t0 = time.time()

                        try:
                            level_result = method_fn(
                                url=url,
                                proxy=proxy,
                                timeout=self.request_timeout,
                                cookie_manager=self.cookie_manager,
                                ad_blocker=self.ad_blocker,
                                popup_closer=self.popup_closer,
                            )
                        except Exception as exc:
                            level_result = {"success": False, "error": str(exc)}

                        latency = time.time() - t0

                        # Per-level timeout check
                        if latency > per_level_timeout:
                            logger.warning(
                                "⏰ %s exceeded level timeout (%.1fs > %ds)",
                                level_name, latency, per_level_timeout,
                            )
                            level_result = {"success": False, "error": "level timeout exceeded"}

                        ok = bool(level_result.get("success"))
                        level_cf = bool(level_result.get("cf_detected"))

                        if level_cf:
                            cf_detected = True

                        # ── Record stats ───────────────────────────────────
                        if self.site_learner:
                            self.site_learner.record_result(
                                domain=domain,
                                method=level_name,
                                success=ok,
                                latency=latency,
                                cf_detected=level_cf,
                                status_code=level_result.get("status_code"),
                            )
                        if self.proxy_manager and proxy:
                            self.proxy_manager.report_result(
                                proxy=proxy,
                                domain=domain,
                                success=ok,
                                latency=latency,
                                cloudflare_blocked=level_cf,
                            )

                        attempts_log.append({
                            "method": level_name,
                            "level": level_int,
                            "proxy": proxy,
                            "attempt": attempt + 1,
                            "success": ok,
                            "latency_s": round(latency, 3),
                            "cf_detected": level_cf,
                            "error": level_result.get("error"),
                        })

                        if ok:
                            # ── Success — build full result ─────────────
                            html = level_result.get("html", "")
                            parsed = _parse_html(html, url)
                            result.update({
                                "success": True,
                                "url": level_result.get("url", url),
                                "status_code": level_result.get("status_code"),
                                "html": html,
                                "text": parsed["text"],
                                "title": parsed["title"],
                                "meta": parsed["meta"],
                                "links": parsed["links"],
                                "images": parsed["images"],
                                "scripts": parsed["scripts"],
                                "cookies": level_result.get("cookies", {}),
                                "headers": level_result.get("headers", {}),
                                "method": f"L{level_int}:{level_name}",
                                "level": level_int,
                                "cf_detected": cf_detected,
                                "duration": sum(a["latency_s"] for a in attempts_log),
                                "attempts": attempts_log,
                                "error": None,
                            })

                            # ── Apply extraction tiers ───────────────
                            result["data"] = _apply_extraction(
                                html=html,
                                url=result["url"],
                                extract=extract,
                                extractor=extractor,
                                prompt=prompt,
                            )

                            logger.info(
                                "✅ Success via %s (proxy=%s, %.2fs)",
                                level_name, proxy or "direct", latency,
                            )
                            return result

                        # CF jump — inject L8 next in the deque
                        if level_cf and self.headful_on_cf:
                            logger.info(
                                "🛡️ CF detected on %s → injecting L8 (uc_headful) next",
                                domain,
                            )
                            if "L8_uc_headful" not in chain:
                                chain.appendleft("L8_uc_headful")
                            break

                finally:
                    if self.proxy_manager and proxy and lease_proxies:
                        self.proxy_manager.release_proxy(proxy, domain)
                
                if cf_detected and level_name not in CF_INCAPABLE:
                    logger.info("⚡ CF challenged. Bailing %s, jumping to L8", level_name)
                    break

            # All proxy attempts for this level failed → next level from deque

        # All levels exhausted
        result["attempts"] = attempts_log
        result["cf_detected"] = cf_detected
        result["error"] = "All bypass levels exhausted"
        result["duration"] = sum(a["latency_s"] for a in attempts_log)

        # Detect if the failure is due to missing optional extras
        import_errors = [
            a for a in attempts_log
            if a.get("error") and "not installed" in a["error"]
        ]
        if import_errors and len(import_errors) == len(attempts_log):
            raise MissingExtrasError(
                needed="browser automation extras",
                install_cmd='pip install "ghost-bypass[full]"',
                detail=(
                    "All attempted levels require optional packages. "
                    "None of playwright, undetected-chromedriver, httpx, "
                    "or curl_cffi are installed."
                ),
            )
        if cf_detected and import_errors:
            # CF was detected but the levels that could handle it need extras
            missing_extras = set()
            for a in import_errors:
                err = a["error"]
                if "playwright" in err:
                    missing_extras.add('pip install "ghost-bypass[playwright]"')
                if "undetected_chromedriver" in err:
                    missing_extras.add('pip install "ghost-bypass[selenium]"')
            if missing_extras:
                raise MissingExtrasError(
                    needed="browser extras for Cloudflare bypass",
                    install_cmd=" OR ".join(sorted(missing_extras)),
                    detail=(
                        f"Cloudflare was detected on {url} but the required "
                        f"browser packages are not installed."
                    ),
                )

        logger.error("❌ %s — all levels failed for %s", result["error"], url)
        return result

    # ── Parallel multi-URL scraping ───────────────────────────────────────

    def scrape_many(
        self,
        urls: List[str],
        workers: int = 5,
        domain_delay: Union[float, Tuple[float, float]] = 0.0,
        **kwargs,
    ) -> List[dict]:
        """
        Scrape multiple URLs in parallel using a thread pool.

        Parameters
        ----------
        urls:
            List of URLs to scrape.
        workers:
            Number of parallel threads (default 5).
        domain_delay:
            Minimum seconds to wait between requests to the same domain 
            to prevent IP bans. Can be a float (e.g., 2.0) or a tuple 
            for a random range (e.g., (2.0, 5.0)). (default 0.0).
        **kwargs:
            Passed to ``scrape()`` (e.g. extract, prompt, extractor).

        Returns
        -------
        list[dict]
            Results in the same order as *urls*.
        """
        import time
        import random
        import urllib.parse
        from threading import Lock

        domain_locks = {}
        last_req = {}
        global_lock = Lock()

        def _get_domain_lock(domain: str) -> Lock:
            with global_lock:
                if domain not in domain_locks:
                    domain_locks[domain] = Lock()
                return domain_locks[domain]

        def _worker(url: str):
            domain = urllib.parse.urlparse(url).netloc
            
            # Resolve delay logic
            delay = 0.0
            if isinstance(domain_delay, tuple) and len(domain_delay) == 2:
                delay = random.uniform(domain_delay[0], domain_delay[1])
            elif isinstance(domain_delay, (int, float)):
                delay = float(domain_delay)
                
            # Query ML Brain for recommended delay and take the maximum
            ml_delay = 0.0
            if self.site_learner:
                ml_delay = self.site_learner.get_recommended_delay(domain)
            delay = max(delay, ml_delay)

            if delay > 0:
                lock = _get_domain_lock(domain)
                with lock:
                    with global_lock:
                        last = last_req.get(domain, 0)
                    now = time.time()
                    elapsed = now - last
                    if elapsed < delay:
                        time.sleep(delay - elapsed)
                    with global_lock:
                        last_req[domain] = time.time()
            return self.scrape(url, **kwargs)

        results = [None] * len(urls)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(_worker, url): i
                for i, url in enumerate(urls)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    results[idx] = {
                        "success": False,
                        "url": urls[idx],
                        "error": str(exc),
                    }
        return results

    # ── Convenience wrappers ──────────────────────────────────────────────

    def get_html(self, url: str, **kwargs) -> str:
        """Return the page HTML or empty string on failure."""
        return self.scrape(url, **kwargs).get("html", "")

    def get_text(self, url: str, **kwargs) -> str:
        """Return the plain text of a page."""
        return self.scrape(url, **kwargs).get("text", "")

    def get_links(self, url: str, **kwargs) -> List[str]:
        """Return all absolute links found on a page."""
        return self.scrape(url, **kwargs).get("links", [])

    def get_images(self, url: str, **kwargs) -> List[str]:
        """Return all absolute image URLs found on a page."""
        return self.scrape(url, **kwargs).get("images", [])

    # ── Engine internals ──────────────────────────────────────────────────

    def _build_chain(self, domain: str) -> List[str]:
        """Use SiteLearner if available; otherwise default order."""
        if self.site_learner:
            return self.site_learner.get_chain(domain)
        return list(LEVELS)


# ═══════════════════════════════════════════════════════════════════════════
#  L0 — requests basic
# ═══════════════════════════════════════════════════════════════════════════

def _L0_requests_basic(url, proxy, timeout, **_) -> dict:
    """Plain requests with realistic browser headers."""
    try:
        import requests
        from ghost_bypass.support.stealth import StealthConfig
        headers = {
            "User-Agent": StealthConfig.random_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0",
        }
        proxies = {"http": proxy, "https": proxy} if proxy else None
        sess = requests.Session()
        resp = sess.get(url, headers=headers, proxies=proxies,
                        timeout=timeout, allow_redirects=True)
        cf = _is_cf_response(resp.status_code, resp.text)
        if resp.status_code in (403, 503) or cf:
            return {"success": False, "cf_detected": cf, "status_code": resp.status_code}
        return {
            "success": True,
            "html": resp.text,
            "url": resp.url,
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "cookies": dict(resp.cookies),
            "cf_detected": False,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  L1 — curl_cffi TLS fingerprint
# ═══════════════════════════════════════════════════════════════════════════

def _L1_requests_tls(url, proxy, timeout, **_) -> dict:
    """curl_cffi: mimics Chrome TLS fingerprint byte-for-byte."""
    try:
        from curl_cffi import requests as cffi_req
        proxies = {"http": proxy, "https": proxy} if proxy else None
        resp = cffi_req.get(
            url,
            impersonate="chrome120",
            proxies=proxies,
            timeout=timeout,
        )
        cf = _is_cf_response(resp.status_code, resp.text)
        if cf or resp.status_code in (403, 503):
            return {"success": False, "cf_detected": cf, "status_code": resp.status_code}
        return {
            "success": True,
            "html": resp.text,
            "url": str(resp.url),
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "cookies": dict(resp.cookies),
        }
    except ImportError:
        logger.debug("[L1] curl_cffi not installed — skipping")
        return {"success": False, "error": "curl_cffi not installed"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  L2 — httpx with HTTP/2
# ═══════════════════════════════════════════════════════════════════════════

def _L2_httpx_http2(url, proxy, timeout, **_) -> dict:
    """httpx with HTTP/2 support — bypasses some H1-only bot checks."""
    try:
        import httpx
        from ghost_bypass.support.stealth import StealthConfig
        headers = {"User-Agent": StealthConfig.random_ua()}
        proxy_url = proxy if proxy else None
        with httpx.Client(http2=True, proxies=proxy_url,
                          timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        cf = _is_cf_response(resp.status_code, resp.text)
        if cf or resp.status_code in (403, 503):
            return {"success": False, "cf_detected": cf, "status_code": resp.status_code}
        return {
            "success": True,
            "html": resp.text,
            "url": str(resp.url),
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
        }
    except ImportError:
        return {"success": False, "error": "httpx not installed"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  L3 — Playwright headless + stealth
# ═══════════════════════════════════════════════════════════════════════════

def _L3_playwright_stealth(url, proxy, timeout, cookie_manager, ad_blocker, **_) -> dict:
    return _playwright_base(
        url, proxy, timeout, cookie_manager, ad_blocker,
        headless=True, mobile=False,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  L4 — Playwright headful + stealth
# ═══════════════════════════════════════════════════════════════════════════

def _L4_playwright_headful(url, proxy, timeout, cookie_manager, ad_blocker, **_) -> dict:
    return _playwright_base(
        url, proxy, timeout, cookie_manager, ad_blocker,
        headless=False, mobile=False,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  L5 — Playwright mobile headless
# ═══════════════════════════════════════════════════════════════════════════

def _L5_playwright_mobile_headless(url, proxy, timeout, cookie_manager, ad_blocker, **_) -> dict:
    return _playwright_base(
        url, proxy, timeout, cookie_manager, ad_blocker,
        headless=True, mobile=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  L6 — Playwright mobile headful
# ═══════════════════════════════════════════════════════════════════════════

def _L6_playwright_mobile_headful(url, proxy, timeout, cookie_manager, ad_blocker, **_) -> dict:
    return _playwright_base(
        url, proxy, timeout, cookie_manager, ad_blocker,
        headless=False, mobile=True,
    )


def _playwright_base(url, proxy, timeout, cookie_manager, ad_blocker,
                     headless: bool, mobile: bool) -> dict:
    """Shared Playwright runner used by L3–L6."""
    try:
        from playwright.sync_api import sync_playwright
        from ghost_bypass.support.stealth import StealthConfig
        from ghost_bypass.cloudflare.handler import CloudflareHandler
        try:
            from playwright_stealth import stealth_sync
            has_stealth_lib = True
        except ImportError:
            has_stealth_lib = False

        with sync_playwright() as pw:
            proxy_cfg = {"server": proxy} if proxy else None
            ua = StealthConfig.random_ua(mobile=mobile)
            vp = {"width": 390, "height": 844} if mobile else StealthConfig.random_viewport()

            browser = pw.chromium.launch(
                headless=headless,
                proxy=proxy_cfg,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                ],
            )
            ctx = browser.new_context(
                user_agent=ua,
                viewport=vp,
                locale="en-US",
                timezone_id="America/New_York",
                is_mobile=mobile,
                has_touch=mobile,
                proxy=proxy_cfg,
            )
            page = ctx.new_page()

            StealthConfig.inject(page, mobile=mobile)
            if has_stealth_lib:
                try:
                    stealth_sync(page)
                except Exception:
                    pass

            if cookie_manager:
                cookie_manager.load_playwright(page, url)

            page.goto(url, wait_until="networkidle",
                      timeout=timeout * 1000)

            cf = CloudflareHandler.is_challenge_page_playwright(page)
            if cf:
                CloudflareHandler.wait_for_challenge_resolution_playwright(page, timeout=60)
                cf = CloudflareHandler.is_challenge_page_playwright(page)

            if ad_blocker:
                ad_blocker.handle_playwright(page, url)

            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass  # best-effort, don't fail if network doesn't idle
            html = page.content()
            final_url = page.url
            cookies = {c["name"]: c["value"] for c in ctx.cookies()}

            if cookie_manager:
                cookie_manager.save_playwright(page, url)

            browser.close()

        still_cf = _is_cf_html(html)
        return {
            "success": not still_cf,
            "html": html,
            "url": final_url,
            "cookies": cookies,
            "cf_detected": cf or still_cf,
        }
    except ImportError:
        return {"success": False, "error": "playwright not installed"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  L7 — Undetected ChromeDriver headless
# ═══════════════════════════════════════════════════════════════════════════

def _L7_uc_headless(url, proxy, timeout, **kwargs) -> dict:
    return _uc_base(url, proxy, timeout, headless=True, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
#  L8 — Undetected ChromeDriver headful (best for CF Turnstile)
# ═══════════════════════════════════════════════════════════════════════════

def _L8_uc_headful(url, proxy, timeout, **kwargs) -> dict:
    return _uc_base(url, proxy, timeout, headless=False, **kwargs)


def _uc_base(url, proxy, timeout, headless: bool,
             cookie_manager=None, ad_blocker=None, popup_closer=None, **_) -> dict:
    """Shared UndetectedChrome runner used by L7–L8."""
    try:
        import undetected_chromedriver as uc
        import threading
        from ghost_bypass.cloudflare.handler import CloudflareHandler
        from ghost_bypass.support.stealth import StealthConfig

        opts = uc.ChromeOptions()
        if headless:
            opts.add_argument("--headless=new")
        if proxy:
            opts.add_argument(f"--proxy-server={proxy}")
        opts.add_argument(f"--user-agent={StealthConfig.random_ua()}")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")

        driver = uc.Chrome(options=opts, use_subprocess=True)
        driver.set_page_load_timeout(timeout)

        lock = threading.Lock()
        if popup_closer:
            popup_closer.start_monitoring(driver, lock, interval=2.0)

        try:
            if cookie_manager:
                driver.get(url)
                cookie_manager.load_selenium(driver, url)

            driver.get(url)
            time.sleep(2)

            cf = CloudflareHandler.is_challenge_page_selenium(driver)
            if cf:
                CloudflareHandler.wait_for_challenge_resolution_selenium(driver, timeout=90)
                cf = CloudflareHandler.is_challenge_page_selenium(driver)

            if ad_blocker:
                ad_blocker.handle_selenium(driver, url)

            time.sleep(1)
            html = driver.page_source
            final_url = driver.current_url
            cookies = {c["name"]: c["value"] for c in driver.get_cookies()}

            if cookie_manager:
                cookie_manager.save_selenium(driver, url)
        finally:
            if popup_closer:
                popup_closer.stop_monitoring()
            driver.quit()

        still_cf = _is_cf_html(html)
        return {
            "success": not still_cf,
            "html": html,
            "url": final_url,
            "cookies": cookies,
            "cf_detected": cf or still_cf,
        }
    except ImportError:
        return {"success": False, "error": "undetected_chromedriver not installed"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  L9 — DrissionPage
# ═══════════════════════════════════════════════════════════════════════════

def _L9_drission(url, proxy, timeout, **_) -> dict:
    """DrissionPage: Chromium + requests hybrid, very hard to detect."""
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        opts = ChromiumOptions()
        opts.headless(True)
        if proxy:
            opts.set_proxy(proxy)
        page = ChromiumPage(addr_or_opts=opts)
        page.get(url, timeout=timeout)
        time.sleep(2)
        html = page.html
        final_url = page.url
        page.quit()
        cf = _is_cf_html(html)
        return {"success": not cf, "html": html, "url": final_url, "cf_detected": cf}
    except ImportError:
        return {"success": False, "error": "DrissionPage not installed"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  L10 — requests-html (pyppeteer JS render)
# ═══════════════════════════════════════════════════════════════════════════

def _L10_requests_html(url, proxy, timeout, **_) -> dict:
    """requests-html: renders JavaScript with pyppeteer."""
    try:
        from requests_html import HTMLSession
        from ghost_bypass.support.stealth import StealthConfig
        sess = HTMLSession()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        resp = sess.get(
            url,
            headers={"User-Agent": StealthConfig.random_ua()},
            proxies=proxies,
            timeout=timeout,
        )
        resp.html.render(timeout=timeout, sleep=2, keep_page=False)
        html = resp.html.html
        cf = _is_cf_html(html)
        return {
            "success": not cf and resp.status_code < 400,
            "html": html,
            "url": str(resp.url),
            "status_code": resp.status_code,
            "cf_detected": cf,
        }
    except ImportError:
        return {"success": False, "error": "requests_html not installed"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  L11 — mechanize (classic HTTP for legacy sites)
# ═══════════════════════════════════════════════════════════════════════════

def _L11_mechanize(url, proxy, timeout, **_) -> dict:
    """mechanize: full browser simulation for legacy sites (no JS)."""
    try:
        import mechanize
        br = mechanize.Browser()
        br.set_handle_equiv(True)
        br.set_handle_redirect(True)
        br.set_handle_referer(True)
        br.set_handle_robots(False)
        br.set_handle_refresh(mechanize._http.HTTPRefreshProcessor(), max_time=1)
        br.addheaders = [
            ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/124 Safari/537.36"),
            ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
            ("Accept-Language", "en-US,en;q=0.5"),
        ]
        if proxy:
            br.set_proxies({"http": proxy, "https": proxy})
        resp = br.open(url, timeout=timeout)
        html = resp.read().decode("utf-8", errors="replace")
        cf = _is_cf_html(html)
        return {
            "success": not cf,
            "html": html,
            "url": resp.geturl(),
            "cf_detected": cf,
        }
    except ImportError:
        return {"success": False, "error": "mechanize not installed"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  Dispatch table
# ═══════════════════════════════════════════════════════════════════════════

_METHOD_DISPATCH: Dict[str, Callable] = {
    "L0_requests_basic":          _L0_requests_basic,
    "L1_requests_tls":            _L1_requests_tls,
    "L2_httpx_http2":             _L2_httpx_http2,
    "L3_playwright_stealth":      _L3_playwright_stealth,
    "L4_playwright_headful":      _L4_playwright_headful,
    "L5_playwright_mobile_headless": _L5_playwright_mobile_headless,
    "L6_playwright_mobile_headful":  _L6_playwright_mobile_headful,
    "L7_uc_headless":             _L7_uc_headless,
    "L8_uc_headful":              _L8_uc_headful,
    "L9_drission":                _L9_drission,
    "L10_requests_html":          _L10_requests_html,
    "L11_mechanize":              _L11_mechanize,
}


# ═══════════════════════════════════════════════════════════════════════════
#  HTML parsing helpers
# ═══════════════════════════════════════════════════════════════════════════

def _parse_html(html: str, base_url: str) -> dict:
    """Extract structured data from raw HTML."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(strip=True) if soup.title else ""
        text = soup.get_text(separator=" ", strip=True)

        meta = {}
        for tag in soup.find_all("meta"):
            name = tag.get("name") or tag.get("property") or ""
            content = tag.get("content", "")
            if name:
                meta[name] = content

        links = []
        for a in soup.find_all("a", href=True):
            abs_url = urljoin(base_url, a["href"])
            if abs_url.startswith("http"):
                links.append(abs_url)

        images = []
        for img in soup.find_all(["img", "source"], src=True):
            abs_url = urljoin(base_url, img["src"])
            if abs_url.startswith("http"):
                images.append(abs_url)
        # Also catch data-src lazy loading
        for img in soup.find_all(attrs={"data-src": True}):
            abs_url = urljoin(base_url, img["data-src"])
            if abs_url.startswith("http"):
                images.append(abs_url)

        scripts = []
        for s in soup.find_all("script", src=True):
            abs_url = urljoin(base_url, s["src"])
            if abs_url.startswith("http"):
                scripts.append(abs_url)

        return {
            "title": title,
            "text": text[:50_000],   # cap at 50 KB
            "meta": meta,
            "links": list(dict.fromkeys(links)),    # deduplicated
            "images": list(dict.fromkeys(images)),
            "scripts": list(dict.fromkeys(scripts)),
        }
    except Exception:
        return {"title": "", "text": "", "meta": {}, "links": [], "images": [], "scripts": []}


# ═══════════════════════════════════════════════════════════════════════════
#  Cloudflare detection helpers
# ═══════════════════════════════════════════════════════════════════════════

_CF_PHRASES = [
    "just a moment", "checking your browser", "enable javascript and cookies",
    "cf_clearance", "cf-browser-verification", "cloudflare ray id",
    "cf-turnstile", "challenge-platform", "ddos-guard",
]


def _is_cf_response(status_code: int, html: str) -> bool:
    if status_code in (403, 503):
        return _is_cf_html(html)
    return False


def _is_cf_html(html: str) -> bool:
    low = html.lower()
    return any(p in low for p in _CF_PHRASES)


# ═══════════════════════════════════════════════════════════════════════════
#  Misc utilities
# ═══════════════════════════════════════════════════════════════════════════

def _domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc.removeprefix("www.")
    except Exception:
        return "unknown"


def _empty_result(url: str) -> dict:
    return {
        "success": False, "url": url, "status_code": None,
        "html": "", "text": "", "title": "", "meta": {}, "links": [],
        "images": [], "scripts": [], "cookies": {}, "headers": {},
        "method": None, "level": None, "cf_detected": False,
        "duration": 0.0, "attempts": [], "data": None, "error": None,
    }


def _inject_level(chain: List[str], level: str, after: str) -> List[str]:
    """Insert *level* immediately after *after* in chain (if not already in)."""
    if level in chain:
        return chain
    try:
        idx = chain.index(after)
        return chain[:idx + 1] + [level] + chain[idx + 1:]
    except ValueError:
        return [level] + chain


# ═══════════════════════════════════════════════════════════════════════════
#  3-Tier Extraction system
# ═══════════════════════════════════════════════════════════════════════════

def _apply_extraction(
    html: str,
    url: str,
    extract: Optional[Dict[str, str]] = None,
    extractor: Optional[Callable] = None,
    prompt: Optional[str] = None,
) -> Any:
    """
    Apply the appropriate extraction tier.

    Priority: extract dict (Tier 1) → extractor fn (Tier 2) → AI prompt (Tier 3)
    If multiple are provided, all are run and results merged.
    """
    data = {}

    # ── Tier 1: CSS/XPath selector dict ────────────────────────────────
    if extract:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for key, selector in extract.items():
                # Try CSS selector first
                try:
                    elements = soup.select(selector)
                    if elements:
                        if len(elements) == 1:
                            data[key] = elements[0].get_text(strip=True)
                        else:
                            data[key] = [el.get_text(strip=True) for el in elements]
                        continue
                except Exception:
                    pass
                # Fallback: try as XPath via lxml
                try:
                    import lxml.html
                    tree = lxml.html.fromstring(html)
                    results = tree.xpath(selector)
                    if results:
                        texts = [
                            r.text_content().strip() if hasattr(r, 'text_content') else str(r)
                            for r in results
                        ]
                        data[key] = texts[0] if len(texts) == 1 else texts
                except Exception:
                    data[key] = None
        except Exception as exc:
            logger.warning("[Engine] Tier 1 extraction error: %s", exc)

    # ── Tier 2: Custom extractor function ─────────────────────────────
    if extractor:
        try:
            tier2_result = extractor(html, url)
            if isinstance(tier2_result, dict):
                data.update(tier2_result)
            else:
                data["extractor_result"] = tier2_result
        except Exception as exc:
            logger.warning("[Engine] Tier 2 extractor error: %s", exc)

    # ── Tier 3: AI-powered extraction ─────────────────────────────────
    if prompt:
        try:
            from ghost_bypass.ai.extractor import ai_extract
            ai_result = ai_extract(html=html, url=url, prompt=prompt)
            if isinstance(ai_result, dict):
                data.update(ai_result)
            else:
                data["ai_result"] = ai_result
        except ImportError:
            logger.warning(
                "[Engine] AI extraction requires: pip install ghost-bypass[ai]"
            )
        except Exception as exc:
            logger.warning("[Engine] Tier 3 AI extraction error: %s", exc)

    return data if data else None
