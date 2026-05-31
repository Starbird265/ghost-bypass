# How to Use ghost_bypass

This guide details the advanced features of `ghost_bypass` and how to leverage the ML-driven components for large-scale, robust scraping.

## 1. Unified Cloudflare Detection

`ghost_bypass` uses a comprehensive phrase-matching engine to detect Cloudflare, DDoS-Guard, and other WAF challenges. The detection engine is unified across both low-level HTTP requests (like `requests` and `httpx`) and high-level stealth browsers (like `playwright` and `undetected_chromedriver`).

When a CF challenge is detected:
1. **Immediate Bailout**: If the current bypass level is incapable of solving JS/Captcha challenges (e.g., plain `requests`), the engine immediately aborts to save proxy bandwidth.
2. **Escalation**: The engine escalates directly to a headful browser level (like `L8_uc_headful`) capable of solving Turnstile or JS challenges.

You don't need to write any custom detection logic; it's entirely handled inside the `BypassEngine`.

## 2. ML Proxy Manager (UCB1 Bandit)

The `MLProxyManager` is responsible for selecting the best proxy for a given request. It doesn't just round-robin; it uses a Multi-Armed Bandit algorithm (UCB1) to balance **exploitation** (using the best proxy) with **exploration** (trying under-tested proxies).

### Domain-Specific Exploration
Crucially, the UCB1 calculation is **domain-aware**. 
* If a proxy has been used 500 times successfully on `google.com` but only 2 times on `example.com`, the engine grants it a high **exploration bonus** for `example.com`. 
* This ensures that proxies aren't starved of trials on new domains just because they have a high global usage count.

```python
from ghost_bypass import MLProxyManager

mgr = MLProxyManager()
mgr.add_proxies(["http://user:pass@proxy1.com", "http://user:pass@proxy2.com"])

# Get the absolute best proxy for this specific domain based on UCB1 scoring
best_proxy = mgr.get_best_proxy(domain="target-site.com")
```

### Domain-Aware Banning
If a proxy hits a Cloudflare block on `site-A.com`, it is banned specifically for `site-A.com` for 1 hour. However, it remains perfectly healthy and available for `site-B.com`.

## 3. The SiteLearner

The `SiteLearner` tracks the history of which bypass methods (L0 through L11) work on which domains.

On your **first visit** to a domain, `ghost_bypass` will start at `L0` (fastest, cheapest) and escalate until it succeeds.
On your **second visit**, the `SiteLearner` ranks the methods using a similar UCB1 algorithm and jumps straight to the method that is most likely to succeed, saving immense amounts of time and proxy bandwidth.

```python
from ghost_bypass import BypassEngine, SiteLearner, MLProxyManager

engine = BypassEngine(
    proxy_manager=MLProxyManager(),
    site_learner=SiteLearner(),
)

# First run might take 15 seconds as it tests L0, L1, then escalates to L8
engine.scrape("https://highly-protected.com")

# Second run on the same domain will jump straight to L8 and take 3 seconds
engine.scrape("https://highly-protected.com/page2")
```

## 4. Parallel Scraping with Adaptive Rate Limiting

When scraping at scale, you can use `scrape_many` to process multiple URLs concurrently. `ghost_bypass` handles all thread safety, proxy leasing, and rate limiting internally.

If a worker encounters an `HTTP 429 Too Many Requests`, the `SiteLearner` automatically increases the base delay for that domain across all active concurrent threads.

```python
from ghost_bypass import BypassEngine

engine = BypassEngine()
urls = [
    "https://example.com/item1",
    "https://example.com/item2",
    "https://example.com/item3",
    # ... hundreds more
]

# Scrape with 10 concurrent workers. 
# Automatically applies a delay between 1.5s and 3.0s between requests to the same domain.
results = engine.scrape_many(
    urls,
    workers=10, 
    domain_delay=(1.5, 3.0) 
)

for res in results:
    if res['success']:
        print(f"Data: {res['title']}")
```

## 5. Three-Tier Extraction

`ghost_bypass` can immediately extract structured data so you don't have to parse HTML manually.

**Tier 1: CSS Selectors**
```python
engine.scrape(url, extract={"title": "h1.product-title", "price": "span.price"})
```

**Tier 2: Custom Python Function**
```python
def extract_logic(html, url):
    return {"length": len(html)}

engine.scrape(url, extractor=extract_logic)
```

**Tier 3: AI Prompt Extraction**
Requires `ghost-bypass[ai]`. Use an LLM to extract complex unstructured data.
```python
engine.scrape(url, prompt="Extract the author name and the publication date.")
```
