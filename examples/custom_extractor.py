#!/usr/bin/env python3
"""
examples/custom_extractor.py
==============================
Pass a custom extractor function to engine.scrape() to get structured
data from any page in a single call.

The extractor receives (html: str, url: str) and can return anything.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ghost_bypass import BypassEngine, SiteLearner, MLProxyManager
from bs4 import BeautifulSoup


# ── Custom extractor ──────────────────────────────────────────────────────
def extract_hacker_news(html: str, url: str) -> list:
    """Extract story titles and scores from Hacker News."""
    soup = BeautifulSoup(html, "html.parser")
    stories = []
    for row in soup.select("tr.athing")[:10]:
        title_tag = row.select_one(".titleline > a")
        score_row = row.find_next_sibling("tr")
        score_tag = score_row.select_one(".score") if score_row else None
        if title_tag:
            stories.append({
                "title": title_tag.get_text(strip=True),
                "link": title_tag.get("href", ""),
                "score": score_tag.get_text(strip=True) if score_tag else "?",
            })
    return stories


# ── Run ────────────────────────────────────────────────────────────────────
engine = BypassEngine(
    proxy_manager=MLProxyManager(data_dir="./data"),
    site_learner=SiteLearner(),
)

result = engine.scrape(
    "https://news.ycombinator.com/",
    extractor=extract_hacker_news,
)

if result["success"]:
    print(f"✅ Method: {result['method']}  ({result['duration']:.2f}s)")
    print("\nTop Hacker News Stories:")
    for i, s in enumerate(result["data"] or [], 1):
        print(f"  {i:2}. [{s['score']:>6}]  {s['title']}")
        print(f"       {s['link'][:80]}")
else:
    print(f"❌ Failed: {result['error']}")
