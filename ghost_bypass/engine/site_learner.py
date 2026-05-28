#!/usr/bin/env python3
"""
ghost_bypass.engine.site_learner
==================================
Per-domain ML method memory using UCB1 (Upper Confidence Bound).

The SiteLearner tracks which bypass levels (L0–L11) have succeeded or
failed for each domain. On the next visit to the same domain it:

  1. Ranks all levels by UCB score (success_rate + exploration bonus)
  2. Returns a **sorted chain** starting from the most likely winner
  3. If the top method fails it drops to the next, and so on

This means the engine gets smarter on every run:
- First visit to a domain: tries levels in default order (L0 → L11)
- Second visit: starts from the level that worked last time
- Over time: accurately predicts the right level for each site

Data is persisted to ``~/.ghost_bypass/site_memory.json``.
"""

import json
import math
import time
import threading
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_PATH = Path.home() / ".ghost_bypass" / "site_memory.json"

# Default escalation order — used for domains with no history
DEFAULT_LEVEL_CHAIN: List[str] = [
    "L0_requests_basic",
    "L1_requests_tls",
    "L2_httpx_http2",
    "L3_playwright_stealth",
    "L4_playwright_headful",
    "L5_playwright_mobile_headless",
    "L6_playwright_mobile_headful",
    "L7_uc_headless",
    "L8_uc_headful",
    "L9_drission",
    "L10_requests_html",
    "L11_mechanize",
]

# Methods that CANNOT bypass Cloudflare (skip if CF is known for domain)
CF_INCAPABLE_METHODS = {
    "L0_requests_basic",
    "L1_requests_tls",
    "L2_httpx_http2",
    "L3_playwright_stealth",
    "L5_playwright_mobile_headless",
    "L10_requests_html",
    "L11_mechanize",
}


class SiteLearner:
    """
    Per-domain method-level memory.

    Each domain stores a dict like::

        {
            "cf_detected": false,
            "js_required": false,
            "last_success_method": "L3_playwright_stealth",
            "last_seen": 1716823456.0,
            "methods": {
                "L0_requests_basic": {
                    "successes": 4,
                    "failures":  1,
                    "total":     5,
                    "avg_latency": 1.2,
                    "last_tried":  1716823400.0,
                    "ban_until":   0
                },
                ...
            }
        }
    """

    def __init__(self, memory_path: Optional[Path] = None):
        self.path = Path(memory_path or DEFAULT_MEMORY_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load()

    # ── Public API ────────────────────────────────────────────────────────

    def get_chain(self, domain: str) -> List[str]:
        """
        Return the ordered method chain for *domain*.

        - Starts from the highest UCB-scored method
        - Skips methods proven to fail for CF-protected domains
        - Falls back to full default chain for new domains
        """
        with self._lock:
            site = self._site(domain)
            methods_data = site.get("methods", {})
            cf_known = site.get("cf_detected", False)

            if not methods_data:
                # Brand new domain — use default order
                chain = list(DEFAULT_LEVEL_CHAIN)
            else:
                chain = self._ucb_ranked(methods_data)

            # Skip CF-incapable methods if we know it's a CF site
            if cf_known:
                chain = [m for m in chain if m not in CF_INCAPABLE_METHODS]
                if not chain:
                    chain = ["L8_uc_headful"]

            # Ensure every level from DEFAULT_LEVEL_CHAIN appears (as fallback)
            known_set = set(chain)
            tail = [m for m in DEFAULT_LEVEL_CHAIN if m not in known_set]
            if cf_known:
                tail = [m for m in tail if m not in CF_INCAPABLE_METHODS]
            chain = chain + tail

            return chain

    def get_last_success_method(self, domain: str) -> Optional[str]:
        """Return the last method that succeeded for *domain*, or None."""
        with self._lock:
            return self._site(domain).get("last_success_method")

    def cf_detected(self, domain: str) -> bool:
        """Return True if Cloudflare has been detected for *domain*."""
        with self._lock:
            return self._site(domain).get("cf_detected", False)

    def record_result(
        self,
        domain: str,
        method: str,
        success: bool,
        latency: float,
        cf_detected: bool = False,
        js_required: bool = False,
        status_code: Optional[int] = None,
    ):
        """
        Record the outcome of a scrape attempt.

        Updates UCB scores, flags Cloudflare detection, persists to disk.
        """
        with self._lock:
            site = self._site(domain)
            site["last_seen"] = time.time()

            if cf_detected:
                site["cf_detected"] = True
            if js_required:
                site["js_required"] = True
            
            # Rate limit adaptation
            current_delay = site.get("recommended_delay", 0.0)
            if status_code == 429:
                site["recommended_delay"] = min(current_delay + 2.0, 30.0)
                logger.debug("[SiteLearner] 🕒 %s returned 429, increasing delay to %.1fs", domain, site["recommended_delay"])
            elif success and current_delay > 0:
                site["recommended_delay"] = max(current_delay * 0.95 - 0.1, 0.0)

            mdata = site["methods"].setdefault(method, {
                "successes": 0,
                "failures": 0,
                "total": 0,
                "avg_latency": 5.0,
                "last_tried": 0,
                "ban_until": 0,
            })

            mdata["total"] += 1
            mdata["last_tried"] = time.time()

            if success:
                mdata["successes"] += 1
                site["last_success_method"] = method
                logger.debug(
                    "[SiteLearner] ✅ %s → %s succeeded (lat=%.2fs)",
                    domain, method, latency,
                )
            else:
                mdata["failures"] += 1
                # Ban methods with < 10% success after 5+ attempts for this domain
                if mdata["total"] >= 5 and mdata["successes"] / mdata["total"] < 0.10:
                    mdata["ban_until"] = time.time() + 86400  # ban for 24h
                    logger.debug(
                        "[SiteLearner] 🚫 %s → %s banned for 24h (too unreliable)",
                        domain, method,
                    )

            # EMA latency update
            alpha = 0.3
            mdata["avg_latency"] = alpha * latency + (1 - alpha) * mdata["avg_latency"]

        self._save()

    def domain_summary(self, domain: str) -> dict:
        """Return a human-readable summary of what we know about *domain*."""
        with self._lock:
            site = self._site(domain)
            return {
                "domain": domain,
                "cf_detected": site.get("cf_detected", False),
                "js_required": site.get("js_required", False),
                "recommended_delay": site.get("recommended_delay", 0.0),
                "last_success_method": site.get("last_success_method"),
                "last_seen": site.get("last_seen"),
                "methods_tried": len(site.get("methods", {})),
            }

    def get_recommended_delay(self, domain: str) -> float:
        """Get the ML-recommended delay (in seconds) for *domain* to prevent rate limits."""
        with self._lock:
            return self._site(domain).get("recommended_delay", 0.0)

    def all_domains(self) -> List[str]:
        """Return list of all domains with stored memory."""
        with self._lock:
            return list(self._data.keys())

    def forget_domain(self, domain: str):
        """Erase all memory for *domain*."""
        with self._lock:
            self._data.pop(domain, None)
        self._save()

    def forget_all(self):
        """Wipe all domain memory."""
        with self._lock:
            self._data.clear()
        self._save()

    def export_json(self, path: str):
        """Export all memory to a JSON file for backup."""
        import json as _json
        with self._lock:
            data_copy = dict(self._data)
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(data_copy, f, indent=2)
        logger.info("[SiteLearner] Exported %d domains to %s", len(data_copy), path)

    def import_json(self, path: str, merge: bool = True):
        """
        Import memory from a JSON file.

        If *merge* is True, imported data is merged with existing memory
        (existing domains are overwritten). If False, existing data is
        replaced entirely.
        """
        import json as _json
        with open(path, encoding="utf-8") as f:
            imported = _json.load(f)
        with self._lock:
            if merge:
                self._data.update(imported)
            else:
                self._data = imported
        self._save()
        logger.info("[SiteLearner] Imported %d domains from %s (merge=%s)", len(imported), path, merge)

    def prune_stale(self, days: int = 30):
        """
        Remove domains not seen in the last *days* days.

        Returns the number of domains pruned.
        """
        import time as _time
        cutoff = _time.time() - (days * 86400)
        pruned = 0
        with self._lock:
            stale = [
                domain for domain, data in self._data.items()
                if (data.get("last_seen") or 0) < cutoff
            ]
            for domain in stale:
                del self._data[domain]
                pruned += 1
        if pruned:
            self._save()
            logger.info("[SiteLearner] Pruned %d stale domains (>%dd old)", pruned, days)
        return pruned

    # ── UCB scoring ───────────────────────────────────────────────────────

    def _ucb_ranked(self, methods_data: dict) -> List[str]:
        """
        Rank methods by UCB1 score.

        UCB score = success_rate
                  + sqrt(2 * ln(total_pulls_across_methods) / pulls_for_this_method)
                  + speed_bonus (lower latency = higher bonus, capped at +0.3)
        """
        now = time.time()
        total_pulls = sum(m.get("total", 0) for m in methods_data.values()) + 1

        scores: List[Tuple[float, str]] = []
        untried: List[str] = []

        for method in DEFAULT_LEVEL_CHAIN:
            mdata = methods_data.get(method)
            if mdata is None:
                untried.append(method)
                continue
            if mdata.get("ban_until", 0) > now:
                continue  # skip temporarily banned methods

            total = mdata.get("total", 0)
            if total == 0:
                untried.append(method)
                continue

            sr = mdata["successes"] / total
            explore = math.sqrt(2 * math.log(total_pulls) / total)
            speed_bonus = max(0.0, (5.0 - mdata.get("avg_latency", 5.0)) * 0.06)
            score = sr + explore + speed_bonus
            scores.append((score, method))

        # Sort descending by score
        scores.sort(key=lambda x: -x[0])
        ranked = [m for _, m in scores]

        # Untried methods follow (still worth exploring)
        return ranked + untried

    # ── Persistence ───────────────────────────────────────────────────────

    def _site(self, domain: str) -> dict:
        """Return (or create) the memory entry for *domain*."""
        if domain not in self._data:
            self._data[domain] = {
                "cf_detected": False,
                "js_required": False,
                "last_success_method": None,
                "last_seen": None,
                "methods": {},
            }
        return self._data[domain]

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.debug(
                    "[SiteLearner] Loaded memory for %d domains", len(self._data)
                )
            except Exception as exc:
                logger.warning("[SiteLearner] Load failed: %s", exc)
                self._data = {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as exc:
            logger.warning("[SiteLearner] Save failed: %s", exc)
