#!/usr/bin/env python3
"""
ghost_bypass.proxy.manager
============================
Domain-aware ML proxy rotation using Upper Confidence Bound (UCB1).

Every proxy maintains TWO levels of statistics:

  Global stats  — overall health across all sites
  Domain stats  — per-(proxy, domain) pair stats

This means a proxy that is Cloudflare-blocked on *site-a.com* is
NOT banned when used on *site-b.com*.

Selection algorithm (``get_best_proxy(domain)``)
-------------------------------------------------
1. Filter out proxies that are globally banned.
2. Filter out proxies banned for *this specific domain*.
3. Score remaining proxies with a blended UCB formula:

      score = 0.4 * global_success_rate
            + 0.6 * domain_success_rate    (if domain data exists)
            + exploration_term
            + speed_bonus                  (lower latency = higher bonus)

4. Return the proxy with the highest score.
   Return ``None`` (direct IP) if nothing is available.

Persistence
-----------
All stats are saved to ``~/.ghost_bypass/proxy_stats.json`` and loaded
automatically on startup. Saving is throttled (random 10 % chance per
report) to avoid I/O on every request.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path.home() / ".ghost_bypass"

# ── Ban durations ──────────────────────────────────────────────────────────
BAN_CF_DOMAIN_SECS = 3_600        # 1 h  — CF block on specific domain
BAN_CF_GLOBAL_SECS = 1_800        # 30 m — global penalty for CF detection
BAN_UNRELIABLE_GLOBAL_SECS = 600  # 10 m — global ban for < 20 % success
BAN_UNRELIABLE_DOMAIN_SECS = 900  # 15 m — domain ban for < 15 % success
BAN_DOMAIN_FAST_SECS = 86_400     # 24 h — domain ban (consistently terrible)


def _now() -> float:
    return time.time()


def _empty_global() -> dict:
    return {
        "tier": "custom",
        "successes": 0,
        "failures": 0,
        "total": 0,
        "avg_latency": 3.0,
        "success_rate": 1.0,
        "ban_until": 0,        # global ban timestamp
        "added_at": _now(),
    }


def _empty_domain_entry() -> dict:
    return {
        "successes": 0,
        "failures": 0,
        "total": 0,
        "avg_latency": 3.0,
        "ban_until": 0,        # domain-specific ban timestamp
        "cf_blocked": False,
        "last_tried": 0,
    }


class MLProxyManager:
    """
    Domain-aware ML proxy manager.

    Parameters
    ----------
    data_dir:
        Directory for ``proxy_stats.json``. Defaults to ``~/.ghost_bypass/``.
    config_file:
        Stats file name inside *data_dir*.
    auto_fetch:
        Automatically scrape free proxies when the pool has < 5 healthy ones.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        config_file: str = "proxy_stats.json",
        auto_fetch: bool = False,
    ):
        _dir = Path(data_dir) if data_dir else DEFAULT_DIR
        _dir.mkdir(parents=True, exist_ok=True)
        self.config_path = _dir / config_file
        # { proxy_url: { "global": {...}, "domains": { domain: {...} } } }
        self._data: Dict[str, dict] = {}
        # (domain, proxy) tuples currently in use by active workers
        self._active_leases: set = set()
        
        self._lock = threading.Lock()
        self._load()
        if auto_fetch and self._healthy_global_count() < 5:
            self.fetch_free_proxies()

    # ── Primary public API ────────────────────────────────────────────────

    def get_best_proxy(
        self,
        domain: Optional[str] = None,
        exclude: Optional[set] = None,
        lease: bool = False,
    ) -> Optional[str]:
        """
        Return the highest-scoring proxy using UCB1.

        Parameters
        ----------
        domain:
            The target domain for domain-specific stats.
        exclude:
            A set of proxies to ignore for this call (e.g. already tried).
        lease:
            If True, mark this proxy as actively in-use for this domain,
            preventing concurrent workers from getting the same proxy.
            You MUST call ``release_proxy()`` when finished.
        """
        exclude = exclude or set()
        with self._lock:
            if not self._data:
                return None

            now = _now()
            total_global_pulls = (
                sum(v["global"]["total"] for v in self._data.values()) + 1
            )
            # Domain-specific total for exploration term blending
            total_domain_pulls = (
                sum(
                    v["domains"].get(domain, {}).get("total", 0)
                    for v in self._data.values()
                ) + 1
            ) if domain else None

            untried_for_domain: List[str] = []
            candidates: List[tuple] = []  # (score, proxy_url)

            for proxy, entry in self._data.items():
                if proxy in exclude:
                    continue
                if domain and (domain, proxy) in self._active_leases:
                    continue

                gl = entry["global"]

                # Skip globally banned
                if gl["ban_until"] > now:
                    continue

                dom = entry["domains"].get(domain) if domain else None

                # Skip domain-banned
                if dom and dom["ban_until"] > now:
                    continue

                # Never tried globally → force exploration
                if gl["total"] == 0:
                    untried_for_domain.append(proxy)
                    continue

                # Never tried for this domain → treat as untried
                if domain and dom is None:
                    untried_for_domain.append(proxy)
                    continue

                score = self._ucb_score(gl, dom, total_global_pulls, total_domain_pulls)
                candidates.append((score, proxy))

            best = None
            # Prefer untried proxies for this domain (exploration)
            if untried_for_domain:
                best = random.choice(untried_for_domain)
            elif candidates:
                candidates.sort(key=lambda x: -x[0])
                best = candidates[0][1]

            if best and lease and domain:
                self._active_leases.add((domain, best))
            
            return best

    def release_proxy(self, proxy: str, domain: Optional[str]):
        """Release a leased proxy so other workers can use it for this domain."""
        if not domain or not proxy:
            return
        with self._lock:
            self._active_leases.discard((domain, proxy))

    def report_result(
        self,
        proxy: str,
        domain: str,
        success: bool,
        latency: float,
        cloudflare_blocked: bool = False,
    ):
        """
        Record the outcome of a request.

        Parameters
        ----------
        proxy:
            The proxy URL used (or ``None`` for direct IP — no-op).
        domain:
            The target domain (e.g. ``"example.com"``).
        success:
            Whether the request succeeded.
        latency:
            Round-trip time in seconds.
        cloudflare_blocked:
            True if the proxy got a CF challenge / 403.
        """
        if not proxy or proxy not in self._data:
            return

        with self._lock:
            entry = self._data[proxy]
            gl = entry["global"]
            dom = entry["domains"].setdefault(domain, _empty_domain_entry())

            # ── Update global stats ───────────────────────────────────────
            gl["total"] += 1
            if success:
                gl["successes"] += 1
            else:
                gl["failures"] += 1
                if cloudflare_blocked:
                    gl["ban_until"] = _now() + BAN_CF_GLOBAL_SECS
                    logger.info(
                        "[Proxy] %s CF-blocked globally → banned %dm",
                        proxy, BAN_CF_GLOBAL_SECS // 60,
                    )
                elif (
                    gl["total"] >= 8
                    and gl["successes"] / gl["total"] < 0.20
                ):
                    gl["ban_until"] = _now() + BAN_UNRELIABLE_GLOBAL_SECS
                    logger.info(
                        "[Proxy] %s globally unreliable → banned %dm",
                        proxy, BAN_UNRELIABLE_GLOBAL_SECS // 60,
                    )

            gl["success_rate"] = gl["successes"] / gl["total"]
            gl["avg_latency"] = _ema(latency, gl["avg_latency"])

            # ── Update domain-specific stats ──────────────────────────────
            dom["total"] += 1
            dom["last_tried"] = _now()
            if success:
                dom["successes"] += 1
            else:
                dom["failures"] += 1
                if cloudflare_blocked:
                    dom["cf_blocked"] = True
                    dom["ban_until"] = _now() + BAN_CF_DOMAIN_SECS
                    logger.info(
                        "[Proxy] %s CF-blocked on %s → domain-banned %dh",
                        proxy, domain, BAN_CF_DOMAIN_SECS // 3600,
                    )
                elif (
                    dom["total"] >= 5
                    and dom["successes"] / dom["total"] < 0.15
                ):
                    ban_dur = (
                        BAN_DOMAIN_FAST_SECS
                        if dom["total"] >= 15
                        else BAN_UNRELIABLE_DOMAIN_SECS
                    )
                    dom["ban_until"] = _now() + ban_dur
                    logger.info(
                        "[Proxy] %s unreliable on %s → domain-banned %dm",
                        proxy, domain, ban_dur // 60,
                    )

            dom["avg_latency"] = _ema(latency, dom["avg_latency"])

        if random.random() < 0.1:
            self._save()

    # ── Pool management ───────────────────────────────────────────────────

    def add_proxy(self, proxy_url: str, tier: str = "custom", _defer_save: bool = False):
        """Add a single proxy to the pool."""
        with self._lock:
            if proxy_url not in self._data:
                self._data[proxy_url] = {
                    "global": {**_empty_global(), "tier": tier},
                    "domains": {},
                }
        if not _defer_save:
            self._save()

    def add_proxies(self, proxy_list: List[str], tier: str = "custom"):
        """Bulk-add a list of proxy URLs (saves once at the end)."""
        for p in proxy_list:
            self.add_proxy(p.strip(), tier=tier, _defer_save=True)
        self._save()

    def remove_proxy(self, proxy_url: str):
        """Permanently remove a proxy from the pool."""
        with self._lock:
            self._data.pop(proxy_url, None)
        self._save()

    def unban_proxy(self, proxy_url: str, domain: Optional[str] = None):
        """
        Lift a ban.

        If *domain* is given, only the domain-specific ban is lifted.
        Otherwise the global ban is lifted.
        """
        with self._lock:
            if proxy_url not in self._data:
                return
            if domain:
                dom = self._data[proxy_url]["domains"].get(domain)
                if dom:
                    dom["ban_until"] = 0
                    logger.info("[Proxy] Unbanned %s for domain %s", proxy_url, domain)
            else:
                self._data[proxy_url]["global"]["ban_until"] = 0
                logger.info("[Proxy] Globally unbanned %s", proxy_url)
        self._save()

    def ban_proxy(
        self,
        proxy_url: str,
        domain: Optional[str] = None,
        duration_secs: int = 3600,
    ):
        """Manually ban a proxy (globally or for a specific domain)."""
        with self._lock:
            if proxy_url not in self._data:
                return
            if domain:
                dom = self._data[proxy_url]["domains"].setdefault(
                    domain, _empty_domain_entry()
                )
                dom["ban_until"] = _now() + duration_secs
            else:
                self._data[proxy_url]["global"]["ban_until"] = _now() + duration_secs
        self._save()

    # ── Reporting & inspection ────────────────────────────────────────────

    def list_proxies(
        self,
        domain: Optional[str] = None,
        only_healthy: bool = False,
    ) -> List[dict]:
        """
        Return all proxies with stats.

        Parameters
        ----------
        domain:
            If given, include domain-specific stats for this domain.
        only_healthy:
            If True, exclude globally or domain-banned proxies.
        """
        now = _now()
        rows = []
        for url, entry in self._data.items():
            gl = entry["global"]
            globally_healthy = gl["ban_until"] <= now

            dom_data = entry["domains"].get(domain) if domain else None
            domain_healthy = (dom_data is None) or (dom_data["ban_until"] <= now)

            is_healthy = globally_healthy and domain_healthy

            if only_healthy and not is_healthy:
                continue

            row = {
                "proxy": url,
                "tier": gl["tier"],
                "healthy": is_healthy,
                "globally_banned": not globally_healthy,
                "global_total": gl["total"],
                "global_success_rate": round(gl["success_rate"], 3),
                "global_avg_latency_s": round(gl["avg_latency"], 2),
                "global_ban_until": _fmt_ban(gl["ban_until"]),
            }
            if domain and dom_data:
                row["domain"] = domain
                row["domain_total"] = dom_data["total"]
                row["domain_success_rate"] = (
                    round(dom_data["successes"] / dom_data["total"], 3)
                    if dom_data["total"] > 0 else None
                )
                row["domain_avg_latency_s"] = round(dom_data["avg_latency"], 2)
                row["domain_cf_blocked"] = dom_data["cf_blocked"]
                row["domain_banned"] = not domain_healthy
                row["domain_ban_until"] = _fmt_ban(dom_data["ban_until"])

            rows.append(row)

        rows.sort(key=lambda r: (not r["healthy"], -r["global_success_rate"]))
        return rows

    def get_banned_proxies(self, domain: Optional[str] = None) -> List[dict]:
        """
        Return all currently-banned proxies.

        If *domain* is given, includes proxies banned specifically for that
        domain even if they are globally healthy.
        """
        now = _now()
        banned = []
        for url, entry in self._data.items():
            gl = entry["global"]
            if gl["ban_until"] > now:
                banned.append({
                    "proxy": url,
                    "scope": "global",
                    "ban_until": _fmt_ban(gl["ban_until"]),
                    "reason": "CF-blocked" if gl.get("cf_blocked") else "unreliable",
                })
            if domain:
                dom = entry["domains"].get(domain)
                if dom and dom["ban_until"] > now:
                    banned.append({
                        "proxy": url,
                        "scope": f"domain:{domain}",
                        "ban_until": _fmt_ban(dom["ban_until"]),
                        "cf_blocked": dom["cf_blocked"],
                    })
        return banned

    def best_for_domain(self, domain: str, top_n: int = 5) -> List[dict]:
        """Return the top *n* proxies ranked for *domain* by combined UCB score."""
        now = _now()
        total_pulls = sum(
            v["global"]["total"] for v in self._data.values()
        ) + 1
        total_domain_pulls = sum(
            v["domains"].get(domain, {}).get("total", 0)
            for v in self._data.values()
        ) + 1

        ranked = []
        for url, entry in self._data.items():
            gl = entry["global"]
            if gl["ban_until"] > now:
                continue
            dom = entry["domains"].get(domain)
            if dom and dom["ban_until"] > now:
                continue
            score = self._ucb_score(gl, dom, total_pulls, total_domain_pulls)
            ranked.append({"proxy": url, "score": round(score, 4)})

        ranked.sort(key=lambda r: -r["score"])
        return ranked[:top_n]

    def pool_summary(self) -> dict:
        """Return a quick health summary of the proxy pool."""
        now = _now()
        total = len(self._data)
        globally_healthy = sum(
            1 for v in self._data.values() if v["global"]["ban_until"] <= now
        )
        globally_banned = total - globally_healthy
        free = sum(
            1 for v in self._data.values() if v["global"]["tier"] == "free"
        )
        custom = total - free
        return {
            "total": total,
            "healthy": globally_healthy,
            "banned": globally_banned,
            "free_tier": free,
            "custom_tier": custom,
        }

    def domain_proxy_report(self, domain: str) -> dict:
        """Full report of what every proxy has done on *domain*."""
        now = _now()
        proxies = []
        for url, entry in self._data.items():
            dom = entry["domains"].get(domain)
            gl = entry["global"]
            proxies.append({
                "proxy": url,
                "domain_tried": dom is not None,
                "domain_successes": dom["successes"] if dom else 0,
                "domain_failures": dom["failures"] if dom else 0,
                "domain_cf_blocked": dom["cf_blocked"] if dom else False,
                "domain_avg_latency": round(dom["avg_latency"], 2) if dom else None,
                "domain_banned": (dom["ban_until"] > now) if dom else False,
                "global_healthy": gl["ban_until"] <= now,
            })
        proxies.sort(
            key=lambda r: (
                not r["global_healthy"],
                r["domain_banned"],
                -(r["domain_successes"] / max(r["domain_successes"] + r["domain_failures"], 1)),
            )
        )
        return {"domain": domain, "proxies": proxies, "total": len(proxies)}

    # ── Free proxy scraping ───────────────────────────────────────────────

    def fetch_free_proxies(self):
        """Scrape public proxy lists and add them to the pool (batch-save)."""
        import requests as _req
        logger.info("[Proxy] Fetching free public proxies…")
        added = 0

        sources = [
            # proxyscrape API
            (
                "proxyscrape",
                "https://api.proxyscrape.com/v2/"
                "?request=displayproxies&protocol=http"
                "&timeout=10000&country=all&ssl=all&anonymity=all",
            ),
            # TheSpeedX GitHub list
            (
                "speedx",
                "https://raw.githubusercontent.com/TheSpeedX/"
                "PROXY-List/master/http.txt",
            ),
            # clarketm GitHub list
            (
                "clarketm",
                "https://raw.githubusercontent.com/clarketm/"
                "proxy-list/master/proxy-list-raw.txt",
            ),
        ]

        for name, url in sources:
            try:
                resp = _req.get(url, timeout=10)
                if resp.status_code == 200:
                    for line in resp.text.splitlines()[:80]:
                        line = line.strip()
                        if line and ":" in line:
                            self.add_proxy(f"http://{line}", tier="free", _defer_save=True)
                            added += 1
            except Exception as exc:
                logger.debug("[Proxy] %s source failed: %s", name, exc)

        # Single save after all proxies added
        self._save()

        if added:
            logger.info("[Proxy] Added %d free proxies", added)
        else:
            logger.warning("[Proxy] No free proxies fetched — will use direct IP")

    # ── UCB scoring ───────────────────────────────────────────────────────

    @staticmethod
    def _ucb_score(
        gl: dict,
        dom: Optional[dict],
        total_pulls: int,
        total_domain_pulls: Optional[int] = None,
    ) -> float:
        """
        Blended UCB1 score.

        If domain data exists, both the success rate AND the exploration
        term are blended (0.4 global + 0.6 domain) so that proxies
        rarely tried on the target domain get a proper exploration bonus.
        """
        gl_total = gl["total"]
        if gl_total == 0:
            return float("inf")  # untried globally → force exploration

        gl_sr = gl["successes"] / gl_total
        explore = math.sqrt(2 * math.log(total_pulls) / gl_total)
        speed_bonus = max(0.0, (5.0 - gl["avg_latency"]) * 0.08)

        if dom and dom["total"] > 0:
            dom_sr = dom["successes"] / dom["total"]
            sr = 0.4 * gl_sr + 0.6 * dom_sr

            # Blend exploration term with domain-specific counts
            if total_domain_pulls and total_domain_pulls > 1:
                dom_explore = math.sqrt(
                    2 * math.log(total_domain_pulls) / dom["total"]
                )
                explore = 0.4 * explore + 0.6 * dom_explore

            # Blend latency too
            lat = 0.4 * gl["avg_latency"] + 0.6 * dom["avg_latency"]
            speed_bonus = max(0.0, (5.0 - lat) * 0.08)
        else:
            sr = gl_sr

        return sr + explore + speed_bonus

    # ── Persistence ───────────────────────────────────────────────────────

    def _healthy_global_count(self) -> int:
        now = _now()
        return sum(
            1 for v in self._data.values()
            if v["global"]["ban_until"] <= now
            and v["global"]["success_rate"] > 0.2
        )

    def _load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    raw = json.load(f)
                # Support loading old flat format (backwards compat)
                migrated: Dict[str, dict] = {}
                for url, entry in raw.items():
                    if "global" in entry:
                        migrated[url] = entry
                    else:
                        # Old flat format — wrap it
                        migrated[url] = {
                            "global": {**_empty_global(), **entry},
                            "domains": {},
                        }
                self._data = migrated
                logger.debug(
                    "[Proxy] Loaded %d proxies from %s",
                    len(self._data), self.config_path,
                )
            except Exception as exc:
                logger.warning("[Proxy] Load failed: %s", exc)
                self._data = {}

    def _save(self):
        # NOTE: Do NOT acquire self._lock here — callers like report_result()
        # already hold it, and threading.Lock is not re-entrant.
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as exc:
            logger.error("[Proxy] Save failed: %s", exc)

    # ── File import/export ─────────────────────────────────────────────────

    def import_from_file(self, path: str):
        """Load proxies from a newline-delimited file (one proxy URL per line)."""
        p = Path(path)
        if not p.exists():
            logger.warning("[Proxy] File not found: %s", path)
            return 0
        lines = p.read_text(encoding="utf-8").splitlines()
        added = 0
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                if not line.startswith("http"):
                    line = f"http://{line}"
                self.add_proxy(line, tier="custom", _defer_save=True)
                added += 1
        self._save()
        logger.info("[Proxy] Imported %d proxies from %s", added, path)
        return added

    def export_to_file(self, path: str):
        """Export all proxy URLs to a file (one per line)."""
        with open(path, "w", encoding="utf-8") as f:
            for url in self._data:
                f.write(url + "\n")
        logger.info("[Proxy] Exported %d proxies to %s", len(self._data), path)

    def reset_stats(self, proxy_url: str):
        """Reset all stats for a single proxy (keep it in the pool)."""
        with self._lock:
            if proxy_url in self._data:
                tier = self._data[proxy_url]["global"].get("tier", "custom")
                self._data[proxy_url] = {
                    "global": {**_empty_global(), "tier": tier},
                    "domains": {},
                }
        self._save()

    def ping_all(self, timeout: int = 5) -> Dict[str, bool]:
        """Test all proxies with a HEAD request and auto-ban dead ones."""
        import requests as _req
        results = {}
        test_url = "https://httpbin.org/get"
        for proxy_url in list(self._data.keys()):
            try:
                resp = _req.head(
                    test_url,
                    proxies={"http": proxy_url, "https": proxy_url},
                    timeout=timeout,
                )
                alive = resp.status_code < 500
            except Exception:
                alive = False
            results[proxy_url] = alive
            if not alive:
                self.ban_proxy(proxy_url, duration_secs=1800)
                logger.info("[Proxy] %s failed ping → banned 30m", proxy_url)
        return results


# ── Helpers ───────────────────────────────────────────────────────────────

def _ema(new_val: float, old_val: float, alpha: float = 0.3) -> float:
    """Exponential Moving Average."""
    return alpha * new_val + (1 - alpha) * old_val


def _fmt_ban(ts: float) -> Optional[str]:
    """Format a ban timestamp as 'Xm Ys' remaining, or None if not banned."""
    if ts <= _now():
        return None
    remaining = int(ts - _now())
    m, s = divmod(remaining, 60)
    return f"{m}m {s}s"
