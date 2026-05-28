#!/usr/bin/env python3
"""
examples/ml_full_example.py
============================
Full stack example:
  - MLProxyManager   — domain-aware proxy rotation (UCB1)
  - SiteLearner      — per-domain method memory (UCB1)
  - BypassEngine     — L0→L11 auto-escalation

On the FIRST run:
  tries L0, L1, L2... until something works.

On SECOND and subsequent runs for the SAME domain:
  jumps directly to the level that worked last time.
"""

import sys, os, json, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from ghost_bypass import BypassEngine, SiteLearner, MLProxyManager

# ── Setup ────────────────────────────────────────────────────────────────
proxy_mgr   = MLProxyManager(data_dir="./data")   # learns per domain
site_learn  = SiteLearner()                        # remembers best level

engine = BypassEngine(
    proxy_manager=proxy_mgr,
    site_learner=site_learn,
)

# ── Scrape ────────────────────────────────────────────────────────────────
url = "https://httpbin.org/get"

print(f"\n📡 Scraping: {url}")
print(f"   Stored domain summary: {site_learn.domain_summary('httpbin.org')}")
print(f"   Proxy pool: {proxy_mgr.pool_summary()}")
print()

result = engine.scrape(url)

# ── Results ───────────────────────────────────────────────────────────────
print("\n── Result ──────────────────────────────────────────────────────")
print(f"  Success : {result['success']}")
print(f"  Method  : {result['method']}")
print(f"  Level   : L{result['level']}")
print(f"  CF      : {result['cf_detected']}")
print(f"  Duration: {result['duration']:.2f}s")
print(f"  Title   : {result['title']!r}")
print(f"  Links   : {len(result['links'])}")
print(f"  Images  : {len(result['images'])}")
print()

print("── Attempt log ─────────────────────────────────────────────────")
for a in result["attempts"]:
    icon = "✅" if a["success"] else "❌"
    print(f"  {icon} {a['method']:35s}  proxy={a['proxy'] or 'direct':20s}  {a['latency_s']:.2f}s")

print()
print("── SiteLearner memory (after this run) ─────────────────────────")
print(json.dumps(site_learn.domain_summary("httpbin.org"), indent=2))

print()
print("── Top proxies for httpbin.org ─────────────────────────────────")
for row in proxy_mgr.best_for_domain("httpbin.org", top_n=5):
    print(f"  {row['proxy']:40s}  score={row['score']}")
