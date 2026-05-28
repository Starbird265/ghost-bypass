#!/usr/bin/env python3
"""
examples/proxy_report.py
=========================
Inspect the proxy pool:
  - Overall summary
  - All banned proxies
  - Best proxies for a specific domain
  - Full domain report
"""

import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ghost_bypass import MLProxyManager

mgr = MLProxyManager(data_dir="./data")

print("╔══ Proxy Pool Summary ══════════════════════════════════════════╗")
summary = mgr.pool_summary()
for k, v in summary.items():
    print(f"  {k:15s}: {v}")

print("\n╠══ Banned Proxies (global) ════════════════════════════════════╣")
banned = mgr.get_banned_proxies()
if banned:
    for b in banned:
        print(f"  {b['proxy']:40s}  scope={b['scope']}  expires={b['ban_until']}")
else:
    print("  (none)")

domain = "example.com"
print(f"\n╠══ Banned Proxies for {domain} ══════════════════════════════╣")
banned_domain = mgr.get_banned_proxies(domain=domain)
if banned_domain:
    for b in banned_domain:
        print(f"  {b['proxy']:40s}  CF={b.get('cf_blocked')}  expires={b['ban_until']}")
else:
    print("  (none — no domain-specific bans)")

print(f"\n╠══ Best Proxies for {domain} ════════════════════════════════╣")
best = mgr.best_for_domain(domain, top_n=5)
if best:
    for row in best:
        print(f"  score={row['score']:.4f}  {row['proxy']}")
else:
    print("  (no ranked proxies yet — need more data)")

print(f"\n╠══ Full Proxy List ════════════════════════════════════════════╣")
all_proxies = mgr.list_proxies(domain=domain)
for row in all_proxies[:10]:
    status = "✅" if row["healthy"] else "🚫"
    print(
        f"  {status}  {row['proxy']:40s}"
        f"  global_sr={row['global_success_rate']:.2f}"
        f"  lat={row['global_avg_latency_s']:.1f}s"
        + (f"  domain_sr={row.get('domain_success_rate', '-')}" if domain in row else "")
    )

print("\n╚══ Done ════════════════════════════════════════════════════════╝")
