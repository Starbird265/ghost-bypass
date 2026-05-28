#!/usr/bin/env python3
"""
ghost_bypass.support.stealth
==============================
Browser anti-detection patches.

Injects JavaScript overrides that mask automation signals from both
Playwright and Selenium WebDriver. Works with any website.
"""

import random
import time
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── UA database with matching platform + Sec-CH-UA metadata ─────────────────
# Each entry: (user_agent, platform, sec_ch_ua, sec_ch_ua_platform)

_DESKTOP_UA_ENTRIES = [
    # Chrome 124 — Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Win32",
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        '"Windows"',
    ),
    # Chrome 125 — Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Win32",
        '"Chromium";v="125", "Google Chrome";v="125", "Not-A.Brand";v="99"',
        '"Windows"',
    ),
    # Chrome 126 — Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Win32",
        '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
        '"Windows"',
    ),
    # Chrome 124 — macOS
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "MacIntel",
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        '"macOS"',
    ),
    # Chrome 125 — macOS
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "MacIntel",
        '"Chromium";v="125", "Google Chrome";v="125", "Not-A.Brand";v="99"',
        '"macOS"',
    ),
    # Chrome 126 — macOS
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "MacIntel",
        '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
        '"macOS"',
    ),
    # Chrome 124 — Linux
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Linux x86_64",
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        '"Linux"',
    ),
    # Chrome 126 — Linux
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Linux x86_64",
        '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
        '"Linux"',
    ),
    # Edge 124 — Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        "Win32",
        '"Chromium";v="124", "Microsoft Edge";v="124", "Not-A.Brand";v="99"',
        '"Windows"',
    ),
    # Edge 125 — Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
        "Win32",
        '"Chromium";v="125", "Microsoft Edge";v="125", "Not-A.Brand";v="99"',
        '"Windows"',
    ),
    # Firefox 126 — Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
        "Win32",
        None,  # Firefox doesn't send Sec-CH-UA
        None,
    ),
    # Firefox 127 — Windows
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
        "Win32",
        None,
        None,
    ),
    # Firefox 126 — macOS
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:126.0) Gecko/20100101 Firefox/126.0",
        "MacIntel",
        None,
        None,
    ),
    # Firefox 127 — Linux
    (
        "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
        "Linux x86_64",
        None,
        None,
    ),
    # Safari 17.5 — macOS
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "MacIntel",
        None,
        None,
    ),
    # Safari 17.4 — macOS
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        "MacIntel",
        None,
        None,
    ),
]

_MOBILE_UA_ENTRIES = [
    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
        "iPhone",
        None,
        None,
    ),
    (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "iPhone",
        None,
        None,
    ),
    (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Linux armv81",
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        '"Android"',
    ),
    (
        "Mozilla/5.0 (Linux; Android 14; SM-S924B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
        "Linux armv81",
        '"Chromium";v="126", "Google Chrome";v="126", "Not-A.Brand";v="99"',
        '"Android"',
    ),
    (
        "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Linux armv81",
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        '"Android"',
    ),
]


def _build_stealth_js(platform: str) -> str:
    """Generate the core stealth JS with the correct navigator.platform."""
    return f"""
// ── Remove webdriver flag ───────────────────────────────────────────────
Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});

// ── Realistic plugin list ───────────────────────────────────────────────
Object.defineProperty(navigator, 'plugins', {{
    get: () => {{
        const arr = [
            {{ name: 'Chrome PDF Plugin',   filename: 'internal-pdf-viewer' }},
            {{ name: 'Chrome PDF Viewer',   filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' }},
            {{ name: 'Native Client',       filename: 'internal-nacl-plugin' }},
        ];
        arr.__proto__ = PluginArray.prototype;
        return arr;
    }}
}});

// ── Language & locale ───────────────────────────────────────────────────
Object.defineProperty(navigator, 'languages', {{ get: () => ['en-US', 'en'] }});
Object.defineProperty(navigator, 'language',  {{ get: () => 'en-US' }});

// ── Hardware hints ──────────────────────────────────────────────────────
Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => 8 }});
Object.defineProperty(navigator, 'deviceMemory',        {{ get: () => 8 }});

// ── Platform (matched to User-Agent) ────────────────────────────────────
Object.defineProperty(navigator, 'platform', {{ get: () => '{platform}' }});

// ── Fake window.chrome ──────────────────────────────────────────────────
window.chrome = {{
    app:      {{ isInstalled: false }},
    webstore: {{ onInstallStageChanged: {{}}, onDownloadProgress: {{}} }},
    runtime:  {{
        PlatformOs:  {{ MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' }},
        PlatformArch: {{ ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' }},
        RequestUpdateCheckStatus: {{ THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' }},
        OnInstalledReason: {{ INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' }},
        OnRestartRequiredReason: {{ APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' }},
        connect: function() {{}},
        sendMessage: function() {{}},
    }},
    csi:       function() {{}},
    loadTimes: function() {{}},
}};

// ── Permissions API ─────────────────────────────────────────────────────
const _origPerms = window.navigator.permissions.query;
window.navigator.permissions.query = (p) =>
    p.name === 'notifications'
        ? Promise.resolve({{ state: Notification.permission }})
        : _origPerms(p);

// ── WebGL vendor/renderer spoof ─────────────────────────────────────────
const _getParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {{
    if (param === 37445) return 'Intel Inc.';           // UNMASKED_VENDOR_WEBGL
    if (param === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
    return _getParam.call(this, param);
}};

// ── Correct iframe contentWindow ────────────────────────────────────────
Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {{
    get: function() {{
        return window;
    }}
}});

// ── Canvas fingerprint noise ────────────────────────────────────────────
(function() {{
    const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {{
        const ctx = this.getContext('2d');
        if (ctx) {{
            const r = Math.floor(Math.random() * 10) - 5;
            const imgData = ctx.getImageData(0, 0, Math.min(this.width, 2), Math.min(this.height, 2));
            if (imgData.data.length > 0) {{
                imgData.data[0] = imgData.data[0] + r;
                ctx.putImageData(imgData, 0, 0);
            }}
        }}
        return _toDataURL.apply(this, arguments);
    }};
}})();
"""


_STEALTH_JS_MOBILE = """
Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 5 });
"""


class StealthConfig:
    """
    Inject JS anti-detection patches into any browser automation context.

    Playwright usage::

        StealthConfig.inject(page)       # call before page.goto()

    Selenium usage::

        StealthConfig.inject_selenium(driver)
    """

    # Expose flat UA lists for backwards compatibility
    DESKTOP_UAS = [entry[0] for entry in _DESKTOP_UA_ENTRIES]
    MOBILE_UAS = [entry[0] for entry in _MOBILE_UA_ENTRIES]

    VIEWPORTS = [
        {"width": 1920, "height": 1080},
        {"width": 1440, "height": 900},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1280, "height": 800},
        {"width": 2560, "height": 1440},
    ]

    # ── Current session state (set by random_ua_full) ─────────────────────
    _current_entry: Optional[Tuple] = None

    @classmethod
    def random_ua_full(cls, mobile: bool = False) -> Tuple[str, str, Optional[str], Optional[str]]:
        """
        Pick a random UA and return (ua, platform, sec_ch_ua, sec_ch_ua_platform).

        Also stores the entry for headers() to use.
        """
        entries = _MOBILE_UA_ENTRIES if mobile else _DESKTOP_UA_ENTRIES
        entry = random.choice(entries)
        cls._current_entry = entry
        return entry

    @classmethod
    def random_ua(cls, mobile: bool = False) -> str:
        """Return a random user-agent string."""
        ua, _, _, _ = cls.random_ua_full(mobile=mobile)
        return ua

    @classmethod
    def sec_ch_ua_headers(cls) -> Dict[str, str]:
        """
        Return Sec-CH-UA headers matching the last random_ua_full() call.

        Only returns headers for Chromium-based UAs (Firefox/Safari don't
        send these).
        """
        entry = cls._current_entry
        if not entry:
            return {}
        _, _, sec_ch_ua, sec_ch_ua_platform = entry
        headers = {}
        if sec_ch_ua:
            headers["Sec-CH-UA"] = sec_ch_ua
            headers["Sec-CH-UA-Mobile"] = "?1" if "Mobile" in entry[0] else "?0"
        if sec_ch_ua_platform:
            headers["Sec-CH-UA-Platform"] = sec_ch_ua_platform
        return headers

    @staticmethod
    def inject(page, mobile: bool = False):
        """Inject stealth scripts into a Playwright page (call before goto)."""
        try:
            # Pick a fresh UA and get its matched platform
            _, platform, _, _ = StealthConfig.random_ua_full(mobile=mobile)
            page.add_init_script(_build_stealth_js(platform))
            if mobile:
                page.add_init_script(_STEALTH_JS_MOBILE)
            logger.debug("[Stealth] Injected into Playwright page (platform=%s)", platform)
        except Exception as exc:
            logger.warning("[Stealth] Playwright inject failed: %s", exc)

    @staticmethod
    def inject_selenium(driver, mobile: bool = False):
        """Inject stealth JS into a Selenium WebDriver."""
        try:
            _, platform, _, _ = StealthConfig.random_ua_full(mobile=mobile)
            driver.execute_script(_build_stealth_js(platform))
            if mobile:
                driver.execute_script(_STEALTH_JS_MOBILE)
            logger.debug("[Stealth] Injected into Selenium driver (platform=%s)", platform)
        except Exception as exc:
            logger.warning("[Stealth] Selenium inject failed: %s", exc)

    @staticmethod
    def simulate_human_mouse(page):
        """Move mouse to a few random positions to appear human."""
        try:
            vp = page.viewport_size or {"width": 1366, "height": 768}
            for _ in range(random.randint(2, 5)):
                x = random.randint(80, vp["width"] - 80)
                y = random.randint(80, vp["height"] - 80)
                page.mouse.move(x, y)
                time.sleep(random.uniform(0.04, 0.18))
        except Exception:
            pass

    @classmethod
    def random_viewport(cls) -> dict:
        return random.choice(cls.VIEWPORTS)
