# ghost_bypass

> **Advanced ML-Guided Anti-Bot Evasion and Stealth Scraping Framework**

Scrape any website. Works on Cloudflare-protected sites, WAFs, anti-bot systems, GDPR walls, and plain HTTP sites — automatically choosing the right technique.

---

## ✨ What makes it different

| Feature | ghost_bypass |
|---------|-------------|
| **ML level selection** | UCB1 bandit remembers which bypass level works per domain |
| **Domain-aware proxies** | Proxy A banned on site-X ≠ Proxy A banned on site-Y |
| **12 bypass levels (L0–L11)** | Auto-escalates from fast → stealthy → headful browser |
| **CF jump logic** | Detects Cloudflare → immediately promotes to headful UC |
| **Versatile extraction** | Returns HTML, text, links, images, meta — works on any site |
| **Custom extractors** | Pass your own `fn(html, url)` to get structured data in one call |
| **Zero config** | Works out of the box with `BypassEngine()` (raises clear errors if optional extras are missing) |

---

## Installation

```bash
# Minimum (requests only — L0, L11)
pip install ghost-bypass

# With playwright + selenium + TLS fingerprinting (recommended)
pip install "ghost-bypass[full]"

# Specific extras
pip install "ghost-bypass[playwright]"   # L3–L6
pip install "ghost-bypass[selenium]"     # L7–L8
pip install "ghost-bypass[tls]"          # L1–L2
```

After installing Playwright extras:
```bash
playwright install chromium
```

---

## Quick start

```python
from ghost_bypass import BypassEngine

engine = BypassEngine()
result = engine.scrape("https://any-website.com/page/")

print(result['success'])   # True
print(result['method'])    # "L0:L0_requests_basic"
print(result['html'])      # full page HTML
print(result['links'])     # all absolute links
print(result['images'])    # all image URLs
print(result['title'])     # page title
```

---

## Full ML stack (recommended)

```python
from ghost_bypass import BypassEngine, SiteLearner, MLProxyManager

engine = BypassEngine(
    proxy_manager=MLProxyManager(),   # domain-aware UCB proxy rotation
    site_learner=SiteLearner(),       # per-domain level memory
)

result = engine.scrape("https://cloudflare-protected-site.com/")
```

**First run** → tries L0, L1, L2… until success.
**Second run** → jumps directly to what worked (e.g. L3), skipping slower levels.
**CF detected** → immediately jumps to L8 (headful UC with turnstile support).

---

## Bypass levels (L0 → L11)

| Level | Name | Technology | CF bypass |
|-------|------|-----------|-----------|
| **L0** | `requests_basic` | `requests` + real headers | ❌ |
| **L1** | `requests_tls` | `curl_cffi` Chrome TLS fingerprint | ⚠️ partial |
| **L2** | `httpx_http2` | `httpx` HTTP/2 | ❌ |
| **L3** | `playwright_stealth` | Playwright headless + stealth JS | ⚠️ partial |
| **L4** | `playwright_headful` | Playwright **visible** + stealth JS | ✅ most sites |
| **L5** | `playwright_mobile_headless` | Mobile emulation, headless | ⚠️ |
| **L6** | `playwright_mobile_headful` | Mobile emulation, **visible** | ✅ |
| **L7** | `uc_headless` | Undetected ChromeDriver headless | ✅ |
| **L8** | `uc_headful` | Undetected ChromeDriver **visible** + Turnstile | ✅✅ best |
| **L9** | `drission` | DrissionPage Chromium hybrid | ✅ |
| **L10** | `requests_html` | pyppeteer JS rendering | ⚠️ partial |
| **L11** | `mechanize` | Classic HTTP (legacy sites) | ❌ |

---

## Result dict

```python
result = engine.scrape(url)

result['success']      # bool
result['url']          # final URL after all redirects
result['status_code']  # HTTP status (or None for browser methods)
result['html']         # full page HTML
result['text']         # plain text (stripped HTML)
result['title']        # <title> tag content
result['meta']         # {name: content} for all <meta> tags
result['links']        # deduplicated list of absolute <a href> links
result['images']       # deduplicated list of absolute <img src> URLs
result['scripts']      # absolute <script src> URLs
result['cookies']      # {name: value} dict
result['headers']      # response headers dict
result['method']       # e.g. "L3:L3_playwright_stealth" (format: "L{n}:{level_name}")
result['level']        # integer 0–11
result['cf_detected']  # True if Cloudflare was detected on any attempt
result['duration']     # total seconds across all attempts
result['attempts']     # list of per-attempt detail dicts
result['data']         # custom extractor output (if extractor= provided)
result['error']        # error message if failed, else None
```

---

## Domain-aware proxy rotation

```python
from ghost_bypass import MLProxyManager

mgr = MLProxyManager()

# Add your own proxies
mgr.add_proxies([
    "http://1.2.3.4:8080",
    "http://5.6.7.8:3128",
], tier="custom")

# Optionally fetch free public proxies (commented out because free proxies
# are unreliable against Cloudflare — use your own paid proxies for CF sites)
# mgr.fetch_free_proxies()

# Get best proxy for a specific domain
proxy = mgr.get_best_proxy(domain="example.com")

# Report outcome (feeds the UCB model)
mgr.report_result(
    proxy=proxy,
    domain="example.com",
    success=True,
    latency=1.2,
    cloudflare_blocked=False,
)

# Proxy reports
print(mgr.pool_summary())
print(mgr.best_for_domain("example.com", top_n=5))
print(mgr.get_banned_proxies())
print(mgr.get_banned_proxies(domain="example.com"))

# Unban manually
mgr.unban_proxy("http://1.2.3.4:8080")                     # global
mgr.unban_proxy("http://1.2.3.4:8080", domain="site.com")  # domain only
```

### How domain-aware banning works

```
Proxy "http://1.2.3.4:8080"
 ├── global: healthy (success_rate=0.85)
 ├── example.com: healthy (3 successes, 0 failures)
 ├── cloudflare-site.com: CF-BANNED for 1h (got 403)
 └── slow-site.org: domain-banned for 15m (< 15% success)
```

A proxy banned on `cloudflare-site.com` is **still available** for `example.com`.

---

## Site memory (SiteLearner)

```python
from ghost_bypass import SiteLearner

sl = SiteLearner()

# What does it know about a domain?
print(sl.domain_summary("example.com"))
# {
#   "domain": "example.com",
#   "cf_detected": false,
#   "js_required": false,
#   "last_success_method": "L0_requests_basic",  # level_name format
#   "last_seen": 1716823456.0,
#   "methods_tried": 3
# }

# Get the ML-ranked level chain for a domain
print(sl.get_chain("example.com"))
# ["L0_requests_basic", "L1_requests_tls", "L3_playwright_stealth", ...]
# ^ Uses level_name format (no "L3:" prefix). The "L3:L3_xxx" format
#   appears only in result['method'] after scraping.
# CF-incapable methods are automatically filtered if CF was previously detected

# All domains with stored memory
print(sl.all_domains())

# Erase memory for a domain (reset its chain)
sl.forget_domain("example.com")
```

---

## Thread-Safe Proxy Leasing & Dynamic Delays

To scale high-throughput concurrent scraping without triggering IP blocks or rate limits, `ghost_bypass` implements advanced ML-driven concurrency controls.

### 1. Concurrent Proxy Leasing
When multiple workers run concurrently (e.g. in `scrape_many`), they must not make requests to the same target domain using the same proxy IP at the same time. The `MLProxyManager` enforces a lease mechanism:
* **Lease Acquisition**: When a worker attempts a bypass level, it borrows a highly rated proxy *specifically* leased for that target domain.
* **Exclusion**: Concurrent workers requesting the same domain will automatically bypass the leased proxy and select the next-best alternative.
* **Lease Release**: The proxy is guaranteed to release back to the pool inside a `finally` block once the request succeeds or fails.

You can toggle proxy leasing off if desired:
```python
engine.scrape(url, lease_proxies=False)
```

### 2. Adaptive Rate Limit Pacing
`SiteLearner` monitors target domains for `HTTP 429 (Too Many Requests)` rate-limiting responses.
* **Automatic Backoff**: If a 429 is encountered, `SiteLearner` instantly raises the recommended delay for that domain.
* **Decay**: Over successful cycles, the pacing delay naturally decays back to the minimum configured baseline.
* **Worker Sync**: Concurrent workers in `scrape_many` automatically coordinate using a per-domain thread lock and respect the maximum of either:
  * User-specified custom/random delays (e.g., `domain_delay=(2, 5)`)
  * `SiteLearner`'s adaptive backoff delay.

To invoke concurrent scraping with dynamic pacing:
```python
urls = ["https://site.com/p1", "https://site.com/p2", "https://other.com/p1"]

# Scraping concurrent with 5 workers, custom delay range, and ML pacing
results = engine.scrape_many(
    urls,
    workers=5,
    domain_delay=(2.0, 5.0)  # Random delays between 2 and 5 seconds per domain
)
```

---

## 3-Tier Extraction

Extract structured data from pages immediately, with or without coding.

**Tier 1: CSS selector dictionary**
```python
engine = BypassEngine()
result = engine.scrape("https://shop.example.com/product/", extract={
    "price": ".price",
    "title": "h1"
})
print(result['data'])   # {"price": "$19.99", "title": "Cool Widget"}
```

**Tier 2: Custom Python function**
```python
from bs4 import BeautifulSoup

def my_extractor(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    return {"stock": soup.select_one("#stock").text}

result = engine.scrape(url, extractor=my_extractor)
```

**Tier 3: AI-powered extraction (Requires `ghost-bypass[ai]`)**
Pass a plain English prompt. Auto-detects local models (Ollama, LM Studio) or uses OpenAI/Anthropic/Gemini keys.
```python
result = engine.scrape(url, prompt="extract product name, price, and stock status")
print(result['data'])   # {"name": "Widget", "price": "$19.99", "stock": "In Stock"}
```

---

## Rate limiting & parallel scraping

Scrape multiple URLs in parallel with `scrape_many`. Built-in domain locking prevents IP bans when multiple workers hit the same domain.

```python
from ghost_bypass import BypassEngine

engine = BypassEngine(request_timeout=30)
urls = ["https://site.com/page1", "https://site.com/page2", "https://site.com/page3"]

# 5 parallel workers, but guarantees 2.0s delay between requests to site.com
results = engine.scrape_many(urls, workers=5, domain_delay=2.0)
```

For manual loops, add your own delays:
```python
import time, random
for url in urls:
    result = engine.scrape(url)
    time.sleep(random.uniform(1.0, 3.0))  # min_delay=1.0, max_delay=3.0
```

> **Note:** For aggressive scraping, use `domain_delay=0` and supply a proxy pool to distribute requests across IPs.

---

## The `ghost` CLI

ghost_bypass comes with a powerful CLI for scraping, proxy management, and AI key management.

```bash
# Scrape from the terminal
ghost scrape https://example.com --extract '{"title":"h1","price":".price"}'
ghost scrape https://example.com --prompt "extract product info"

# Parallel scraping
ghost scrape-many https://example.com/1 https://example.com/2 --workers 5

# Manage proxies
ghost proxy fetch       # fetch free proxies
ghost proxy ping        # test all proxies
ghost proxy list        # list healthy proxies

# Manage site memory
ghost memory list       # see which domains have CF detected

# Interactive REPL
ghost repl
# > /scrape https://example.com
# > /extract https://example.com {"title":"h1"}
# > /keys autodetect
```

### AI Keys & Local Models
Use the CLI to manage keys for Tier 3 extraction:
```bash
ghost keys autodetect          # Auto-discover Ollama/LM Studio running locally
ghost keys add openai sk-...   # Add an API key
```
> **Security Note:** API keys are stored in `~/.ghost_bypass/ai_keys.json` using XOR obfuscation. **This is NOT cryptographic encryption** — it only prevents casual plaintext reading. For production security, use environment variables (`OPENAI_API_KEY`, etc.) or a secret vault.

---



## Cookie persistence

```python
from ghost_bypass import BypassEngine, CookieManager

# Cookies auto-expire after ttl_days (default: 7)
cm = CookieManager(ttl_days=7)

engine = BypassEngine(cookie_manager=cm)
result = engine.scrape("https://cf-protected-site.com/")
# On repeat visits, saved cookies skip the CF challenge

# Manage cookies manually
print(cm.list_domains())    # domains with saved cookies
cm.clear("https://example.com")   # clear one domain
cm.clear_all()              # wipe all
```

---

## Ad blocker & popup closer

```python
from ghost_bypass import AdBlocker, PopupCloser

# Playwright
blocker = AdBlocker(max_iterations=5)
blocker.handle_playwright(page, original_url)

# Selenium — blocking mode
closer = PopupCloser()
closer.close_all(driver, original_url)

# Selenium — background thread + JS interval monitor
import threading
lock = threading.Lock()
closer.start_monitoring(driver, lock, interval=2.0)
# ... do your scraping ...
closer.stop_monitoring()
```

---

## Human behavior simulation

`HumanBehavior` is applied **automatically** in headful browser levels (L4, L6, L8)
when using Selenium/UC. It provides Bézier-curve mouse movements, momentum scrolling,
and realistic typing to avoid bot detection.

You can also use it directly:

```python
from ghost_bypass import HumanBehavior

human = HumanBehavior(min_delay=0.08, max_delay=0.45, movement_speed="medium")

# Use with any Selenium driver
human.human_scroll(driver, direction="down", smooth=True)
human.human_click(driver, element, overshoot=True)
human.type_like_human(element, "search query")
human.page_view_pattern(driver, duration=3.0)  # realistic browsing
```

---

## Architecture

```
ghost_bypass/
├── engine/
│   ├── engine.py        ← BypassEngine (L0–L11 dispatch + ML orchestration)
│   └── site_learner.py  ← SiteLearner  (per-domain UCB method memory)
├── proxy/
│   └── manager.py       ← MLProxyManager (domain-aware UCB proxy rotation)
├── cloudflare/
│   └── handler.py       ← CloudflareHandler (detect + wait for CF to resolve)
├── ad_blocker/
│   ├── blocker.py       ← AdBlocker  (overlay/modal/cookie banner closer)
│   └── popup_closer.py  ← PopupCloser (window + JS interval monitor)
└── support/
    ├── stealth.py       ← StealthConfig (anti-bot JS patches)
    ├── cookies.py       ← CookieManager (per-domain persistence, configurable TTL)
    └── human.py         ← HumanBehavior (Bézier mouse, scroll, typing — auto-applied in headful levels)
```

### Escalation flow

```
URL requested
    │
    ├─ SiteLearner.get_chain(domain)  ─→  UCB-ranked level list
    │   (new domain: L0→L11 default order)
    │   (known domain: starts at best-known level)
    │
    └─ For each level in chain:
        │
        ├─ MLProxyManager.get_best_proxy(domain)  ─→  best proxy for this site
        │   (UCB: blends global + domain-specific stats)
        │
        ├─ Run level method (L0 → L11)
        │
        ├─ CF detected?  ──yes──→  inject L8 as next attempt immediately
        │
        ├─ Proxy failed?  ─────→  try next-best proxy for same level
        │
        ├─ Level failed?  ─────→  escalate to next level
        │
        └─ Success?  ──────────→  record stats, return rich result dict
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). PRs welcome — especially new bypass levels!

## License

MIT — see [LICENSE](LICENSE).
