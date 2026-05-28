"""
ghost_bypass — Advanced ML-Guided Anti-Bot Evasion and Stealth Scraping Framework
===================================================================================

Quick start::

    from ghost_bypass import BypassEngine, SiteLearner, MLProxyManager

    engine = BypassEngine(
        proxy_manager=MLProxyManager(),
        site_learner=SiteLearner(),
    )
    result = engine.scrape("https://any-website.com/page/")

    print(result['success'])
    print(result['html'])
    print(result['links'])
    print(result['method'])   # e.g. "L3:playwright_stealth"

    # Tier 1: CSS selector extraction
    result = engine.scrape(url, extract={"title": "h1", "price": ".price"})

    # Tier 3: AI extraction
    result = engine.scrape(url, prompt="extract product name and price")

    # Parallel scraping
    results = engine.scrape_many([url1, url2, url3], workers=5)
"""

from ghost_bypass.engine.engine import BypassEngine, MissingExtrasError, LEVELS
from ghost_bypass.engine.site_learner import SiteLearner
from ghost_bypass.proxy.manager import MLProxyManager
from ghost_bypass.cloudflare.handler import CloudflareHandler
from ghost_bypass.ad_blocker.blocker import AdBlocker
from ghost_bypass.ad_blocker.popup_closer import PopupCloser
from ghost_bypass.support.stealth import StealthConfig
from ghost_bypass.support.cookies import CookieManager
from ghost_bypass.support.human import HumanBehavior

__all__ = [
    "BypassEngine",
    "MissingExtrasError",
    "SiteLearner",
    "MLProxyManager",
    "CloudflareHandler",
    "AdBlocker",
    "PopupCloser",
    "StealthConfig",
    "CookieManager",
    "HumanBehavior",
    "LEVELS",
]

__version__ = "1.1.0"
__author__ = "Gaurav Singh"
__license__ = "MIT"

