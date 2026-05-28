#!/usr/bin/env python3
"""
ghost_bypass.ai.keys
=====================
API key storage, rotation, and management.

Keys are stored in ``~/.ghost_bypass/ai_keys.json`` encrypted with the
machine's hostname as a weak obfuscation layer. For true security, use
environment variables or a vault.
"""

import json
import hashlib
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_KEYS_DIR = Path.home() / ".ghost_bypass"
KEYS_FILE = "ai_keys.json"


def _obfuscate(key: str) -> str:
    """Simple XOR obfuscation (NOT encryption — just prevents casual reading)."""
    hostname = os.uname().nodename.encode()
    seed = hashlib.sha256(hostname).digest()
    result = []
    for i, ch in enumerate(key.encode()):
        result.append(ch ^ seed[i % len(seed)])
    import base64
    return base64.b64encode(bytes(result)).decode()


def _deobfuscate(encoded: str) -> str:
    """Reverse the XOR obfuscation."""
    import base64
    hostname = os.uname().nodename.encode()
    seed = hashlib.sha256(hostname).digest()
    data = base64.b64decode(encoded)
    result = []
    for i, b in enumerate(data):
        result.append(b ^ seed[i % len(seed)])
    return bytes(result).decode()


class KeyManager:
    """
    Manage AI API keys for ghost_bypass.

    Supports multiple providers (openai, anthropic, google, ollama, lmstudio,
    custom local endpoints) and key rotation.

    Usage::

        km = KeyManager()
        km.add("openai", "sk-abc123...")
        km.add("anthropic", "sk-ant-...")
        key = km.get("openai")  # returns the active key
        km.rotate("openai")     # rotates to next key if multiple exist
    """

    def __init__(self, data_dir: Optional[str] = None):
        self._dir = Path(data_dir or DEFAULT_KEYS_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / KEYS_FILE
        self._data: Dict[str, dict] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────

    def add(self, provider: str, key: str, label: Optional[str] = None):
        """
        Add an API key for a provider.

        Multiple keys can be stored per provider for rotation.
        """
        provider = provider.lower().strip()
        if provider not in self._data:
            self._data[provider] = {
                "keys": [],
                "active_index": 0,
                "endpoints": [],
            }

        entry = {
            "key_obfuscated": _obfuscate(key),
            "label": label or f"key-{len(self._data[provider]['keys']) + 1}",
            "added_at": time.time(),
            "last_used": None,
            "usage_count": 0,
        }
        self._data[provider]["keys"].append(entry)
        self._save()
        logger.info("[Keys] Added key for %s (label=%s)", provider, entry["label"])

    def add_local(self, provider: str, endpoint: str, label: Optional[str] = None):
        """
        Register a local AI endpoint (no API key required).

        Works with Ollama, LM Studio, LocalAI, vLLM, text-generation-webui, etc.
        """
        provider = provider.lower().strip()
        if provider not in self._data:
            self._data[provider] = {
                "keys": [],
                "active_index": 0,
                "endpoints": [],
            }
        self._data[provider]["endpoints"].append({
            "url": endpoint.rstrip("/"),
            "label": label or f"local-{len(self._data[provider]['endpoints']) + 1}",
            "added_at": time.time(),
        })
        self._save()
        logger.info("[Keys] Added local endpoint for %s: %s", provider, endpoint)

    def get(self, provider: str) -> Optional[str]:
        """Get the active API key for a provider. Returns None if no key exists."""
        provider = provider.lower().strip()
        pdata = self._data.get(provider)
        if not pdata or not pdata["keys"]:
            return None
        idx = pdata["active_index"] % len(pdata["keys"])
        entry = pdata["keys"][idx]
        entry["last_used"] = time.time()
        entry["usage_count"] += 1
        self._save()
        return _deobfuscate(entry["key_obfuscated"])

    def get_endpoint(self, provider: str) -> Optional[str]:
        """Get the first registered local endpoint for a provider."""
        provider = provider.lower().strip()
        pdata = self._data.get(provider)
        if not pdata or not pdata["endpoints"]:
            return None
        return pdata["endpoints"][0]["url"]

    def remove(self, provider: str, index: int = 0):
        """Remove a specific key by index from a provider."""
        provider = provider.lower().strip()
        pdata = self._data.get(provider)
        if not pdata or index >= len(pdata["keys"]):
            logger.warning("[Keys] No key at index %d for %s", index, provider)
            return
        removed = pdata["keys"].pop(index)
        if pdata["active_index"] >= len(pdata["keys"]):
            pdata["active_index"] = 0
        self._save()
        logger.info("[Keys] Removed key '%s' from %s", removed["label"], provider)

    def remove_provider(self, provider: str):
        """Remove all keys and endpoints for a provider."""
        provider = provider.lower().strip()
        if provider in self._data:
            del self._data[provider]
            self._save()
            logger.info("[Keys] Removed all keys for %s", provider)

    def rotate(self, provider: str):
        """Advance to the next key for a provider (round-robin)."""
        provider = provider.lower().strip()
        pdata = self._data.get(provider)
        if not pdata or len(pdata["keys"]) < 2:
            logger.info("[Keys] Nothing to rotate for %s", provider)
            return
        pdata["active_index"] = (pdata["active_index"] + 1) % len(pdata["keys"])
        self._save()
        new_label = pdata["keys"][pdata["active_index"]]["label"]
        logger.info("[Keys] Rotated %s to key '%s'", provider, new_label)

    def list_providers(self) -> List[str]:
        """Return list of all configured providers."""
        return list(self._data.keys())

    def list_keys(self, provider: str) -> List[dict]:
        """Return key metadata (no secrets) for a provider."""
        provider = provider.lower().strip()
        pdata = self._data.get(provider)
        if not pdata:
            return []
        result = []
        for i, entry in enumerate(pdata["keys"]):
            key_raw = _deobfuscate(entry["key_obfuscated"])
            # Mask the key: show first 4 and last 4 chars
            if len(key_raw) > 8:
                masked = key_raw[:4] + "…" + key_raw[-4:]
            else:
                masked = "****"
            result.append({
                "index": i,
                "label": entry["label"],
                "masked_key": masked,
                "active": i == pdata["active_index"],
                "usage_count": entry["usage_count"],
                "last_used": entry["last_used"],
            })
        return result

    def list_endpoints(self, provider: str) -> List[dict]:
        """Return registered local endpoints for a provider."""
        provider = provider.lower().strip()
        pdata = self._data.get(provider)
        if not pdata:
            return []
        return pdata.get("endpoints", [])

    def summary(self) -> dict:
        """Return a summary of all configured providers and key counts."""
        result = {}
        for provider, pdata in self._data.items():
            result[provider] = {
                "keys": len(pdata["keys"]),
                "endpoints": len(pdata.get("endpoints", [])),
                "active_key": pdata["keys"][pdata["active_index"]]["label"]
                if pdata["keys"] else None,
            }
        return result

    # ── From environment variables ────────────────────────────────────────

    def load_from_env(self):
        """
        Auto-detect API keys from environment variables.

        Checks: OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, etc.
        """
        env_map = {
            "openai": ["OPENAI_API_KEY", "OPENAI_KEY"],
            "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
            "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
            "groq": ["GROQ_API_KEY"],
            "together": ["TOGETHER_API_KEY"],
            "mistral": ["MISTRAL_API_KEY"],
        }
        for provider, env_vars in env_map.items():
            for var in env_vars:
                val = os.environ.get(var)
                if val and val.strip():
                    # Don't add duplicates
                    existing_keys = [
                        _deobfuscate(k["key_obfuscated"])
                        for k in self._data.get(provider, {}).get("keys", [])
                    ]
                    if val.strip() not in existing_keys:
                        self.add(provider, val.strip(), label=f"env:{var}")
                    break

    # ── Persistence ───────────────────────────────────────────────────────

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception as exc:
                logger.warning("[Keys] Load failed: %s", exc)
                self._data = {}

    def _save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as exc:
            logger.error("[Keys] Save failed: %s", exc)
