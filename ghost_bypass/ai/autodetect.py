#!/usr/bin/env python3
"""
ghost_bypass.ai.autodetect
============================
Automatically discover local AI models running on the machine or network.

Scans well-known ports for:
  - Ollama (11434)
  - LM Studio (1234)
  - LocalAI (8080)
  - text-generation-webui / oobabooga (5000, 7860)
  - vLLM (8000)
  - llama.cpp server (8080)
  - Jan.ai (1337)
  - GPT4All (4891)
  - KoboldAI (5001)
  - Custom user-provided endpoints
"""

import json
import logging
import socket
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Well-known local AI ports ──────────────────────────────────────────────
_KNOWN_SERVICES: List[Tuple[str, int, str, str]] = [
    # (name, port, health_path, model_list_path)
    ("ollama", 11434, "/api/tags", "/api/tags"),
    ("lmstudio", 1234, "/v1/models", "/v1/models"),
    ("localai", 8080, "/v1/models", "/v1/models"),
    ("vllm", 8000, "/v1/models", "/v1/models"),
    ("text-generation-webui", 5000, "/api/v1/model", "/api/v1/model"),
    ("text-generation-webui-gradio", 7860, "/", None),
    ("jan", 1337, "/v1/models", "/v1/models"),
    ("gpt4all", 4891, "/v1/models", "/v1/models"),
    ("koboldai", 5001, "/api/v1/model", "/api/v1/model"),
    ("llama-cpp", 8081, "/v1/models", "/v1/models"),
]

# Common hostnames to scan
_SCAN_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]


class AutoDetector:
    """
    Discover local AI services running on the machine or LAN.

    Usage::

        detector = AutoDetector()
        found = detector.scan()
        # [{"name": "ollama", "url": "http://localhost:11434", "models": [...]}]

        # Or scan a custom endpoint
        result = detector.probe("http://192.168.1.100:8080")
    """

    def __init__(self, timeout: float = 2.0, extra_hosts: Optional[List[str]] = None):
        self.timeout = timeout
        self.hosts = list(_SCAN_HOSTS)
        if extra_hosts:
            self.hosts.extend(extra_hosts)

    def scan(self, include_models: bool = True) -> List[dict]:
        """
        Scan all known ports on localhost for running AI services.

        Returns a list of dicts with detected services and their models.
        """
        import urllib.request
        import urllib.error

        found = []
        logger.info("[AutoDetect] Scanning for local AI services…")

        for host in self.hosts:
            for name, port, health_path, model_path in _KNOWN_SERVICES:
                base_url = f"http://{host}:{port}"

                # Quick port check first (faster than HTTP)
                if not self._port_open(host, port):
                    continue

                # Try HTTP health check
                try:
                    url = f"{base_url}{health_path}"
                    req = urllib.request.Request(url, method="GET")
                    req.add_header("User-Agent", "ghost-bypass/1.1.0")
                    resp = urllib.request.urlopen(req, timeout=self.timeout)

                    if resp.status < 400:
                        service = {
                            "name": name,
                            "host": host,
                            "port": port,
                            "url": base_url,
                            "status": "running",
                            "models": [],
                        }

                        # Try to list models
                        if include_models and model_path:
                            service["models"] = self._fetch_models(
                                base_url, model_path, name
                            )

                        found.append(service)
                        logger.info(
                            "[AutoDetect] ✅ Found %s at %s (%d models)",
                            name, base_url, len(service["models"]),
                        )
                except Exception:
                    continue

        if not found:
            logger.info("[AutoDetect] No local AI services detected")
        else:
            logger.info("[AutoDetect] Found %d local AI services", len(found))

        return found

    def probe(self, url: str) -> Optional[dict]:
        """
        Probe a single URL to check if it's a compatible AI endpoint.

        Tries OpenAI-compatible /v1/models, Ollama /api/tags, and raw GET.
        """
        import urllib.request
        import urllib.error

        url = url.rstrip("/")
        logger.info("[AutoDetect] Probing %s…", url)

        # Try common API paths
        paths_to_try = [
            ("/v1/models", "openai-compatible"),
            ("/api/tags", "ollama"),
            ("/api/v1/model", "text-generation-webui"),
            ("/", "generic"),
        ]

        for path, api_type in paths_to_try:
            try:
                test_url = f"{url}{path}"
                req = urllib.request.Request(test_url, method="GET")
                req.add_header("User-Agent", "ghost-bypass/1.1.0")
                resp = urllib.request.urlopen(req, timeout=self.timeout)

                if resp.status < 400:
                    body = resp.read().decode("utf-8", errors="replace")
                    result = {
                        "url": url,
                        "api_type": api_type,
                        "status": "reachable",
                        "models": [],
                    }

                    # Parse model list
                    try:
                        data = json.loads(body)
                        if isinstance(data, dict):
                            if "models" in data:
                                # Ollama format
                                result["models"] = [
                                    m.get("name", m.get("model", "unknown"))
                                    for m in data["models"]
                                ]
                            elif "data" in data:
                                # OpenAI format
                                result["models"] = [
                                    m.get("id", "unknown")
                                    for m in data["data"]
                                ]
                    except (json.JSONDecodeError, TypeError):
                        pass

                    logger.info(
                        "[AutoDetect] ✅ %s is %s (%d models)",
                        url, api_type, len(result["models"]),
                    )
                    return result

            except Exception:
                continue

        logger.info("[AutoDetect] ❌ %s not reachable or not a known AI API", url)
        return None

    def scan_subnet(self, subnet: str = "192.168.1", ports: Optional[List[int]] = None) -> List[dict]:
        """
        Scan a /24 subnet for AI services (slow — use sparingly).

        Parameters
        ----------
        subnet:
            First three octets, e.g. "192.168.1"
        ports:
            Ports to check. Defaults to all known AI ports.
        """
        if ports is None:
            ports = list(set(p for _, p, _, _ in _KNOWN_SERVICES))

        found = []
        logger.info("[AutoDetect] Scanning subnet %s.0/24 (ports: %s)…", subnet, ports)

        for host_id in range(1, 255):
            host = f"{subnet}.{host_id}"
            for port in ports:
                if self._port_open(host, port, timeout=0.3):
                    result = self.probe(f"http://{host}:{port}")
                    if result:
                        result["host"] = host
                        result["port"] = port
                        found.append(result)

        logger.info("[AutoDetect] Subnet scan complete — found %d services", len(found))
        return found

    # ── Private ───────────────────────────────────────────────────────────

    def _port_open(self, host: str, port: int, timeout: Optional[float] = None) -> bool:
        """Quick TCP connect check."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout or self.timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def _fetch_models(self, base_url: str, model_path: str, service_name: str) -> list:
        """Fetch and parse model list from a running service."""
        import urllib.request

        try:
            url = f"{base_url}{model_path}"
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "ghost-bypass/1.1.0")
            resp = urllib.request.urlopen(req, timeout=self.timeout)
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)

            if isinstance(data, dict):
                # Ollama: {"models": [{"name": "llama3:8b"}, ...]}
                if "models" in data:
                    return [m.get("name", m.get("model", "?")) for m in data["models"]]
                # OpenAI compat: {"data": [{"id": "gpt-4"}, ...]}
                if "data" in data:
                    return [m.get("id", "?") for m in data["data"]]
                # text-generation-webui: {"result": "model_name"}
                if "result" in data:
                    return [data["result"]]
            elif isinstance(data, list):
                return [str(m) for m in data[:20]]

        except Exception as exc:
            logger.debug("[AutoDetect] Model list fetch failed for %s: %s", base_url, exc)

        return []
