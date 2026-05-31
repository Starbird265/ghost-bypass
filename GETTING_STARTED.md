# Getting Started with ghost_bypass

Welcome to the `ghost_bypass` quick start guide. This framework provides advanced, machine-learning guided anti-bot evasion that is extremely simple to configure for daily scraping tasks, but scales infinitely for large-scale production scraping.

## 1. Prerequisites

The base library only requires Python 3.8+ and `requests`. However, for modern anti-bot evasion, you will want the full suite of browser tools and compression libraries.

## 2. Installation

Install the library with the `full` extra to get Playwright, Undetected ChromeDriver, and TLS fingerprinting tools:

```bash
pip install "ghost-bypass[full]"
```

**Note on Compression (Brotli)**:
If you are scraping sites that return heavily compressed data (like Brotli), ensure you have the required decoding libraries. Our base request engine now correctly handles standard gzip/deflate natively, but having `brotli` installed optimizes network bandwidth:

```bash
pip install brotli brotlicffi
```

After installing Playwright extras, don't forget to install the required browsers:

```bash
playwright install chromium
```

## 3. Verify Installation

Use the built-in CLI doctor to ensure all necessary dependencies are installed correctly and ready to bypass anti-bot mechanisms:

```bash
ghost doctor
```
*You should see green checks next to requests, playwright, curl-cffi, and brotli.*

## 4. Your First Scrape

The core of `ghost_bypass` is the `BypassEngine`. It will automatically orchestrate different bypass levels (from simple `requests` to stealth browsers) to get the data you need.

Create a file named `scrape_test.py`:

```python
from ghost_bypass import BypassEngine

# Initialize the engine (zero-config by default)
engine = BypassEngine()

# Scrape a protected site
result = engine.scrape("https://example.com")

if result['success']:
    print(f"✅ Successfully scraped: {result['title']}")
    print(f"Using method: {result['method']}")
    print(f"Links found: {len(result['links'])}")
else:
    print(f"❌ Failed to scrape. Error: {result.get('error')}")
```

Run it:
```bash
python scrape_test.py
```

## 5. Next Steps

Now that you've got the basics working, it's time to learn how to scale this up.

Check out the [How To Use Guide](HOW_TO_USE.md) to learn about:
- **ML Proxy Manager (UCB1 Bandit)**: Dynamically selecting proxies based on domain-specific success rates and exploration.
- **SiteLearner**: Remembering which bypass method works best for each domain.
- **Unified Cloudflare Detection**: How the engine automatically detects and escalates CF challenges.
- **Parallel Scraping**: Scraping hundreds of URLs concurrently without triggering rate limits.
