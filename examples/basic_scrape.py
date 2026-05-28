#!/usr/bin/env python3
"""
examples/basic_scrape.py
=========================
Simplest possible usage of ghost_bypass.
No proxy, no ML memory — just a quick scrape.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ghost_bypass import BypassEngine

engine = BypassEngine()

result = engine.scrape("https://httpbin.org/get")

if result["success"]:
    print(f"✅ Success via {result['method']}")
    print(f"   Title : {result['title']}")
    print(f"   HTML  : {len(result['html'])} chars")
    print(f"   Links : {len(result['links'])}")
    print(f"   Took  : {result['duration']:.2f}s")
else:
    print(f"❌ Failed: {result['error']}")
