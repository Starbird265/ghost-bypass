#!/usr/bin/env python3
"""
ghost_bypass.cli
=================
The ``ghost`` CLI — scrape, manage proxies, AI keys, and site memory
from the command line.

Usage::

    ghost scrape https://example.com
    ghost scrape https://example.com --extract '{"title":"h1","price":".price"}'
    ghost scrape https://example.com --prompt "extract product info"
    ghost proxy list
    ghost keys add openai sk-abc...
    ghost keys autodetect
    ghost memory list
    ghost doctor
"""

import json
import sys
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ghost_bypass")


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)-5s │ %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


def _print_json(data, pretty: bool = True):
    """Print data as JSON to stdout."""
    indent = 2 if pretty else None
    print(json.dumps(data, indent=indent, default=str))


def _print_table(rows: list, headers: list):
    """Print a simple ASCII table."""
    if not rows:
        print("  (empty)")
        return
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))

    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print("  ".join("─" * w for w in col_widths))
    for row in rows:
        print(fmt.format(*[str(v) for v in row]))


# ═══════════════════════════════════════════════════════════════════════════
#  Main CLI entry point
# ═══════════════════════════════════════════════════════════════════════════

def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="ghost",
        description="👻 ghost-bypass CLI — stealth scraping from the terminal",
    )
    parser.add_argument("--version", action="store_true", help="Show version")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── ghost scrape ──────────────────────────────────────────────────────
    p_scrape = subparsers.add_parser("scrape", help="Scrape a URL")
    p_scrape.add_argument("url", help="URL to scrape")
    p_scrape.add_argument("--extract", type=str, default=None,
                          help='CSS selector dict as JSON, e.g. \'{"title":"h1","price":".price"}\'')
    p_scrape.add_argument("--prompt", type=str, default=None,
                          help="AI extraction prompt (requires ghost-bypass[ai])")
    p_scrape.add_argument("--json", action="store_true", help="Output full result as JSON")
    p_scrape.add_argument("--html", action="store_true", help="Output raw HTML")
    p_scrape.add_argument("--links", action="store_true", help="Output links only")
    p_scrape.add_argument("--level", type=str, default=None,
                          help="Force a specific level (e.g. L3_playwright_stealth)")
    p_scrape.add_argument("--proxy", type=str, default=None, help="Use a specific proxy")
    p_scrape.add_argument("--timeout", type=int, default=30, help="Request timeout (seconds)")
    p_scrape.add_argument("--output", "-o", type=str, default=None, help="Save output to file")

    # ── ghost scrape-many ─────────────────────────────────────────────────
    p_many = subparsers.add_parser("scrape-many", help="Scrape multiple URLs in parallel")
    p_many.add_argument("urls", nargs="+", help="URLs to scrape")
    p_many.add_argument("--workers", type=int, default=5, help="Parallel workers")
    p_many.add_argument("--extract", type=str, default=None, help="CSS selector dict as JSON")
    p_many.add_argument("--prompt", type=str, default=None, help="AI extraction prompt")
    p_many.add_argument("--output", "-o", type=str, default=None, help="Save output to file")

    # ── ghost proxy ───────────────────────────────────────────────────────
    p_proxy = subparsers.add_parser("proxy", help="Manage proxy pool")
    proxy_sub = p_proxy.add_subparsers(dest="proxy_cmd")

    proxy_sub.add_parser("list", help="List all proxies")
    proxy_sub.add_parser("summary", help="Pool health summary")
    proxy_sub.add_parser("fetch", help="Fetch free public proxies")
    proxy_sub.add_parser("ping", help="Ping-test all proxies")

    p_add = proxy_sub.add_parser("add", help="Add a proxy")
    p_add.add_argument("proxy_url", help="Proxy URL (e.g. http://host:port)")

    p_addfile = proxy_sub.add_parser("add-file", help="Import proxies from a file")
    p_addfile.add_argument("path", help="Path to proxy list file")

    p_export = proxy_sub.add_parser("export", help="Export proxies to a file")
    p_export.add_argument("path", help="Output file path")

    p_ban = proxy_sub.add_parser("ban", help="Ban a proxy")
    p_ban.add_argument("proxy_url", help="Proxy to ban")
    p_ban.add_argument("--duration", type=int, default=3600, help="Ban duration in seconds")

    p_unban = proxy_sub.add_parser("unban", help="Unban a proxy")
    p_unban.add_argument("proxy_url", help="Proxy to unban")

    p_best = proxy_sub.add_parser("best", help="Show best proxies for a domain")
    p_best.add_argument("domain", help="Target domain")

    p_report = proxy_sub.add_parser("report", help="Domain proxy report")
    p_report.add_argument("domain", help="Target domain")

    # ── ghost keys ────────────────────────────────────────────────────────
    p_keys = subparsers.add_parser("keys", help="Manage AI API keys")
    keys_sub = p_keys.add_subparsers(dest="keys_cmd")

    keys_sub.add_parser("list", help="List all configured providers and keys")
    keys_sub.add_parser("autodetect", help="Auto-detect local AI services")
    keys_sub.add_parser("from-env", help="Import keys from environment variables")

    p_kadd = keys_sub.add_parser("add", help="Add an API key")
    p_kadd.add_argument("provider", help="Provider name (openai, anthropic, google, ...)")
    p_kadd.add_argument("key", help="API key value")
    p_kadd.add_argument("--label", type=str, default=None, help="Optional label")

    p_klocal = keys_sub.add_parser("add-local", help="Register a local AI endpoint")
    p_klocal.add_argument("provider", help="Provider name (ollama, lmstudio, ...)")
    p_klocal.add_argument("url", help="Endpoint URL (e.g. http://localhost:11434)")

    p_kremove = keys_sub.add_parser("remove", help="Remove a provider's keys")
    p_kremove.add_argument("provider", help="Provider to remove")

    p_krotate = keys_sub.add_parser("rotate", help="Rotate to next key for a provider")
    p_krotate.add_argument("provider", help="Provider to rotate")

    p_kprobe = keys_sub.add_parser("probe", help="Probe a URL to check if it's an AI endpoint")
    p_kprobe.add_argument("url", help="URL to probe")

    # ── ghost memory ──────────────────────────────────────────────────────
    p_mem = subparsers.add_parser("memory", help="Manage site memory (SiteLearner)")
    mem_sub = p_mem.add_subparsers(dest="memory_cmd")

    mem_sub.add_parser("list", help="List all remembered domains")

    p_mshow = mem_sub.add_parser("show", help="Show memory for a domain")
    p_mshow.add_argument("domain", help="Domain to inspect")

    p_mforget = mem_sub.add_parser("forget", help="Forget a domain")
    p_mforget.add_argument("domain", help="Domain to forget")

    mem_sub.add_parser("forget-all", help="Wipe all site memory")

    p_mprune = mem_sub.add_parser("prune", help="Remove stale domains")
    p_mprune.add_argument("--days", type=int, default=30, help="Max age in days")

    p_mexport = mem_sub.add_parser("export", help="Export memory to JSON")
    p_mexport.add_argument("path", help="Output file path")

    p_mimport = mem_sub.add_parser("import", help="Import memory from JSON")
    p_mimport.add_argument("path", help="Input file path")

    # ── ghost cookies ─────────────────────────────────────────────────────
    p_cookies = subparsers.add_parser("cookies", help="Manage saved cookies")
    cookies_sub = p_cookies.add_subparsers(dest="cookies_cmd")

    cookies_sub.add_parser("list", help="List domains with saved cookies")
    cookies_sub.add_parser("clear-all", help="Delete all saved cookies")

    p_cclear = cookies_sub.add_parser("clear", help="Clear cookies for a domain")
    p_cclear.add_argument("url", help="URL/domain to clear cookies for")

    p_cexport = cookies_sub.add_parser("export", help="Export all cookies")
    p_cexport.add_argument("path", help="Output file path")

    p_cimport = cookies_sub.add_parser("import", help="Import cookies")
    p_cimport.add_argument("path", help="Input file path")

    # ── ghost doctor ──────────────────────────────────────────────────────
    subparsers.add_parser("doctor", help="Check installation and diagnose issues")

    # ── ghost install ─────────────────────────────────────────────────────
    p_install = subparsers.add_parser("install", help="Install optional dependencies")
    p_install.add_argument("extra", nargs="?", default="full",
                           help="Extra to install: full, playwright, selenium, tls, ai")

    # ── ghost repl ────────────────────────────────────────────────────────
    subparsers.add_parser("repl", help="Interactive REPL with slash commands")
    
    return parser

def main():
    parser = _build_parser()
    # ── Parse ─────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if args.version:
        from ghost_bypass import __version__
        print(f"ghost-bypass v{__version__}")
        return

    _setup_logging(getattr(args, "verbose", False))

    if not args.command:
        parser.print_help()
        return

    # ── Dispatch ──────────────────────────────────────────────────────────
    try:
        if args.command == "scrape":
            _cmd_scrape(args)
        elif args.command == "scrape-many":
            _cmd_scrape_many(args)
        elif args.command == "proxy":
            _cmd_proxy(args)
        elif args.command == "keys":
            _cmd_keys(args)
        elif args.command == "memory":
            _cmd_memory(args)
        elif args.command == "cookies":
            _cmd_cookies(args)
        elif args.command == "doctor":
            _cmd_doctor()
        elif args.command == "install":
            _cmd_install(args)
        elif args.command == "repl":
            _cmd_repl()
        else:
            parser.print_help()
    except KeyboardInterrupt:
        print("\n👋 Interrupted")
        sys.exit(1)
    except Exception as exc:
        print(f"❌ Error: {exc}", file=sys.stderr)
        if getattr(args, "verbose", False):
            import traceback
            traceback.print_exc()
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
#  Command implementations
# ═══════════════════════════════════════════════════════════════════════════

def _cmd_scrape(args, engine_override=None):
    from ghost_bypass import BypassEngine, SiteLearner, MLProxyManager

    extract_dict = None
    if args.extract:
        extract_dict = json.loads(args.extract)

    engine = engine_override or BypassEngine(
        proxy_manager=MLProxyManager() if not args.proxy else None,
        site_learner=SiteLearner(),
        request_timeout=args.timeout,
    )

    result = engine.scrape(
        url=args.url,
        extract=extract_dict,
        prompt=args.prompt,
    )

    # Format output
    output = None
    if args.json:
        output = json.dumps(result, indent=2, default=str)
    elif args.html:
        output = result.get("html", "")
    elif args.links:
        output = "\n".join(result.get("links", []))
    else:
        # Default: summary
        lines = [
            f"{'✅' if result['success'] else '❌'} {result['url']}",
            f"Method: {result.get('method', 'N/A')}",
            f"Time:   {result.get('duration', 0):.2f}s",
        ]
        if result.get("title"):
            lines.append(f"Title:  {result['title']}")
        if result.get("data"):
            lines.append(f"Data:   {json.dumps(result['data'], indent=2, default=str)}")
        if result.get("error"):
            lines.append(f"Error:  {result['error']}")
        if result.get("links"):
            lines.append(f"Links:  {len(result['links'])} found")
        output = "\n".join(lines)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"📁 Saved to {args.output}")
    else:
        print(output)


def _cmd_scrape_many(args, engine_override=None):
    from ghost_bypass import BypassEngine, SiteLearner, MLProxyManager

    extract_dict = None
    if args.extract:
        extract_dict = json.loads(args.extract)

    engine = engine_override or BypassEngine(
        proxy_manager=MLProxyManager(),
        site_learner=SiteLearner(),
    )

    results = engine.scrape_many(
        urls=args.urls,
        workers=args.workers,
        extract=extract_dict,
        prompt=args.prompt,
    )

    output = json.dumps(results, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"📁 Saved {len(results)} results to {args.output}")
    else:
        print(output)


def _cmd_proxy(args):
    from ghost_bypass import MLProxyManager

    pm = MLProxyManager()

    if args.proxy_cmd == "list":
        rows = pm.list_proxies()
        if not rows:
            print("No proxies in pool. Run: ghost proxy fetch")
            return
        table_rows = [
            [r["proxy"][:50], r["tier"], "✅" if r["healthy"] else "❌",
             r["global_total"], f"{r['global_success_rate']:.0%}",
             f"{r['global_avg_latency_s']:.1f}s"]
            for r in rows
        ]
        _print_table(table_rows, ["Proxy", "Tier", "Health", "Total", "Rate", "Latency"])

    elif args.proxy_cmd == "summary":
        _print_json(pm.pool_summary())

    elif args.proxy_cmd == "fetch":
        print("🔍 Fetching free proxies…")
        pm.fetch_free_proxies()
        summary = pm.pool_summary()
        print(f"Pool: {summary['total']} total, {summary['healthy']} healthy")

    elif args.proxy_cmd == "ping":
        print("🏓 Pinging all proxies (this may take a while)…")
        results = pm.ping_all()
        alive = sum(1 for v in results.values() if v)
        dead = len(results) - alive
        print(f"Results: {alive} alive, {dead} dead (auto-banned for 30m)")

    elif args.proxy_cmd == "add":
        pm.add_proxy(args.proxy_url)
        print(f"✅ Added {args.proxy_url}")

    elif args.proxy_cmd == "add-file":
        count = pm.import_from_file(args.path)
        print(f"✅ Imported {count} proxies from {args.path}")

    elif args.proxy_cmd == "export":
        pm.export_to_file(args.path)
        print(f"📁 Exported to {args.path}")

    elif args.proxy_cmd == "ban":
        pm.ban_proxy(args.proxy_url, duration_secs=args.duration)
        print(f"🚫 Banned {args.proxy_url} for {args.duration}s")

    elif args.proxy_cmd == "unban":
        pm.unban_proxy(args.proxy_url)
        print(f"✅ Unbanned {args.proxy_url}")

    elif args.proxy_cmd == "best":
        best = pm.best_for_domain(args.domain)
        _print_json(best)

    elif args.proxy_cmd == "report":
        report = pm.domain_proxy_report(args.domain)
        _print_json(report)

    else:
        print("Usage: ghost proxy {list|summary|fetch|ping|add|add-file|export|ban|unban|best|report}")


def _cmd_keys(args):
    from ghost_bypass.ai.keys import KeyManager
    from ghost_bypass.ai.autodetect import AutoDetector

    km = KeyManager()

    if args.keys_cmd == "list":
        providers = km.list_providers()
        if not providers:
            print("No AI keys configured. Run: ghost keys add <provider> <key>")
            return
        for provider in providers:
            keys = km.list_keys(provider)
            endpoints = km.list_endpoints(provider)
            print(f"\n🔑 {provider}")
            for k in keys:
                active = " ← active" if k["active"] else ""
                print(f"   [{k['index']}] {k['label']:20s} {k['masked_key']:20s} used:{k['usage_count']}{active}")
            for ep in endpoints:
                print(f"   🌐 {ep['label']:20s} {ep['url']}")

    elif args.keys_cmd == "autodetect":
        print("🔍 Scanning for local AI services…")
        detector = AutoDetector()
        found = detector.scan()
        if not found:
            print("No local AI services detected.")
            print("\nTry starting Ollama, LM Studio, or LocalAI, then run this again.")
            return
        for svc in found:
            models_str = ", ".join(svc["models"][:5]) if svc["models"] else "no models listed"
            print(f"\n✅ {svc['name']} at {svc['url']}")
            print(f"   Models: {models_str}")
            # Auto-register
            km.add_local(svc["name"], svc["url"])
        print(f"\n📝 Registered {len(found)} local service(s)")

    elif args.keys_cmd == "from-env":
        km.load_from_env()
        summary = km.summary()
        if summary:
            print("✅ Loaded keys from environment:")
            for provider, info in summary.items():
                print(f"   {provider}: {info['keys']} key(s)")
        else:
            print("No API keys found in environment variables.")

    elif args.keys_cmd == "add":
        km.add(args.provider, args.key, label=args.label)
        print(f"✅ Added key for {args.provider}")

    elif args.keys_cmd == "add-local":
        km.add_local(args.provider, args.url)
        print(f"✅ Registered local endpoint: {args.url}")

    elif args.keys_cmd == "remove":
        km.remove_provider(args.provider)
        print(f"✅ Removed all keys for {args.provider}")

    elif args.keys_cmd == "rotate":
        km.rotate(args.provider)
        keys = km.list_keys(args.provider)
        active = next((k for k in keys if k["active"]), None)
        if active:
            print(f"🔄 Rotated to: {active['label']} ({active['masked_key']})")

    elif args.keys_cmd == "probe":
        detector = AutoDetector()
        result = detector.probe(args.url)
        if result:
            _print_json(result)
        else:
            print(f"❌ {args.url} is not reachable or not a known AI API")

    else:
        print("Usage: ghost keys {list|add|add-local|remove|rotate|autodetect|from-env|probe}")


def _cmd_memory(args):
    from ghost_bypass import SiteLearner

    sl = SiteLearner()

    if args.memory_cmd == "list":
        domains = sl.all_domains()
        if not domains:
            print("No domains in memory. Scrape some sites first!")
            return
        for d in sorted(domains):
            summary = sl.domain_summary(d)
            method = summary.get("last_success_method", "?")
            cf = "🛡️" if summary.get("cf_detected") else "  "
            methods_tried = summary.get("methods_tried", 0)
            print(f"  {cf} {d:40s} best={method:30s} tried={methods_tried}")

    elif args.memory_cmd == "show":
        summary = sl.domain_summary(args.domain)
        _print_json(summary)

    elif args.memory_cmd == "forget":
        sl.forget_domain(args.domain)
        print(f"✅ Forgot {args.domain}")

    elif args.memory_cmd == "forget-all":
        sl.forget_all()
        print("✅ All site memory wiped")

    elif args.memory_cmd == "prune":
        count = sl.prune_stale(days=args.days)
        print(f"✅ Pruned {count} stale domains (>{args.days}d old)")

    elif args.memory_cmd == "export":
        sl.export_json(args.path)
        print(f"📁 Exported to {args.path}")

    elif args.memory_cmd == "import":
        sl.import_json(args.path)
        print(f"✅ Imported from {args.path}")

    else:
        print("Usage: ghost memory {list|show|forget|forget-all|prune|export|import}")


def _cmd_cookies(args):
    from ghost_bypass import CookieManager

    cm = CookieManager()

    if args.cookies_cmd == "list":
        domains = cm.list_domains()
        if not domains:
            print("No saved cookies.")
            return
        for d in sorted(domains):
            print(f"  🍪 {d}")

    elif args.cookies_cmd == "clear":
        cm.clear(args.url)
        print(f"✅ Cleared cookies for {args.url}")

    elif args.cookies_cmd == "clear-all":
        cm.clear_all()
        print("✅ All cookies cleared")

    elif args.cookies_cmd == "export":
        cm.export_all(args.path)
        print(f"📁 Exported to {args.path}")

    elif args.cookies_cmd == "import":
        cm.import_all(args.path)
        print(f"✅ Imported from {args.path}")

    else:
        print("Usage: ghost cookies {list|clear|clear-all|export|import}")


def _cmd_doctor():
    """Diagnose installation health."""
    from ghost_bypass import __version__
    print(f"👻 ghost-bypass v{__version__}\n")

    checks = [
        ("requests", "requests"),
        ("beautifulsoup4", "bs4"),
        ("lxml", "lxml"),
        ("playwright", "playwright"),
        ("playwright-stealth", "playwright_stealth"),
        ("undetected-chromedriver", "undetected_chromedriver"),
        ("selenium", "selenium"),
        ("httpx", "httpx"),
        ("curl-cffi", "curl_cffi"),
        ("DrissionPage", "DrissionPage"),
        ("litellm", "litellm"),
    ]

    print("📦 Dependencies:")
    for name, module in checks:
        try:
            mod = __import__(module)
            ver = getattr(mod, "__version__", "✓")
            print(f"  ✅ {name:30s} {ver}")
        except ImportError:
            print(f"  ❌ {name:30s} (not installed)")

    # Check data directory
    data_dir = Path.home() / ".ghost_bypass"
    print(f"\n📁 Data directory: {data_dir}")
    print(f"   Exists: {data_dir.exists()}")

    if data_dir.exists():
        files = list(data_dir.glob("*"))
        print(f"   Files:  {len(files)}")

    # Check proxy pool
    try:
        from ghost_bypass import MLProxyManager
        pm = MLProxyManager()
        summary = pm.pool_summary()
        print(f"\n🌐 Proxy pool: {summary['total']} total, {summary['healthy']} healthy")
    except Exception:
        print("\n🌐 Proxy pool: (error reading)")

    # Check site memory
    try:
        from ghost_bypass import SiteLearner
        sl = SiteLearner()
        domains = sl.all_domains()
        print(f"🧠 Site memory: {len(domains)} domains")
    except Exception:
        print("🧠 Site memory: (error reading)")

    # Check AI keys
    try:
        from ghost_bypass.ai.keys import KeyManager
        km = KeyManager()
        summary = km.summary()
        if summary:
            parts = [f"{k}({v['keys']})" for k, v in summary.items()]
            print(f"🔑 AI keys: {', '.join(parts)}")
        else:
            print("🔑 AI keys: none configured")
    except Exception:
        print("🔑 AI keys: (error reading)")

    # Check local AI
    try:
        from ghost_bypass.ai.autodetect import AutoDetector
        detector = AutoDetector(timeout=1.0)
        found = detector.scan(include_models=False)
        if found:
            names = [s["name"] for s in found]
            print(f"🤖 Local AI: {', '.join(names)}")
        else:
            print("🤖 Local AI: none detected")
    except Exception:
        print("🤖 Local AI: (error checking)")

    print("\n✨ Doctor check complete!")


def _cmd_install(args):
    """Install optional dependencies."""
    import subprocess

    extra = args.extra
    valid = ["full", "playwright", "selenium", "tls", "ai", "dev"]
    if extra not in valid:
        print(f"❌ Unknown extra: {extra}. Valid: {', '.join(valid)}")
        return

    cmd = [sys.executable, "-m", "pip", "install", f"ghost-bypass[{extra}]"]
    print(f"📦 Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    if extra in ("full", "playwright"):
        print("🎭 Installing Playwright browsers…")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)

    print("✅ Installation complete!")


def _cmd_repl():
    """Interactive REPL with slash commands."""
    import shlex
    from ghost_bypass import BypassEngine, SiteLearner, MLProxyManager

    print("👻 ghost-bypass REPL")
    print("Type /<command> to run any CLI command (e.g. /scrape, /proxy list)")
    print("Type /quit to exit.")
    print()

    engine = BypassEngine(
        proxy_manager=MLProxyManager(),
        site_learner=SiteLearner(),
    )
    
    parser = _build_parser()

    while True:
        try:
            line = input("ghost> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Bye!")
            break

        if not line:
            continue

        if line.startswith("/"):
            line = line[1:]

        try:
            args_list = shlex.split(line)
        except ValueError as e:
            print(f"❌ Error parsing command: {e}")
            continue

        if not args_list:
            continue

        cmd = args_list[0].lower()
        if cmd in ("quit", "exit", "q"):
            print("👋 Bye!")
            break
        try:
            args = parser.parse_args(args_list)
        except SystemExit:
            continue

        if not getattr(args, "command", None):
            parser.print_help()
            continue

        try:
            if args.command == "scrape":
                _cmd_scrape(args, engine_override=engine)
            elif args.command == "scrape-many":
                _cmd_scrape_many(args, engine_override=engine)
            elif args.command == "proxy":
                _cmd_proxy(args)
            elif args.command == "keys":
                _cmd_keys(args)
            elif args.command == "memory":
                _cmd_memory(args)
            elif args.command == "cookies":
                _cmd_cookies(args)
            elif args.command == "doctor":
                _cmd_doctor()
            elif args.command == "install":
                _cmd_install(args)
            elif args.command == "repl":
                print("Already in REPL.")
            else:
                parser.print_help()
        except Exception as exc:
            print(f"❌ Error: {exc}")


if __name__ == "__main__":
    main()
