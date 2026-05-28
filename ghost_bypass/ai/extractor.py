#!/usr/bin/env python3
"""
ghost_bypass.ai.extractor
===========================
AI-powered structured data extraction from HTML.

Uses litellm for multi-provider support — works with OpenAI, Anthropic,
Google Gemini, Ollama, LM Studio, and any OpenAI-compatible endpoint.

Install::

    pip install ghost-bypass[ai]
    # or manually: pip install litellm
"""

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Max characters of HTML to send to the AI (prevent token overflow)
MAX_HTML_CHARS = 30_000


def _clean_html(html: str) -> str:
    """Strip scripts, styles, and boilerplate (nav, footer) to prioritize main content."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        
        # Remove definitely useless tags
        for tag in soup(["script", "style", "noscript", "svg", "canvas"]):
            tag.decompose()
            
        # If still too long, aggressively remove boilerplate
        html_str = str(soup)
        if len(html_str) > MAX_HTML_CHARS:
            for tag in soup(["nav", "footer", "header", "aside", "form"]):
                tag.decompose()
            html_str = str(soup)
            
        # Clean up whitespace
        html_str = re.sub(r"\s+", " ", html_str)
        
        if len(html_str) > MAX_HTML_CHARS:
            html_str = html_str[:MAX_HTML_CHARS] + "\n\n[... truncated ...]"
            
        return html_str.strip()
    except ImportError:
        # Fallback to regex if bs4 is somehow missing (though it's a main dependency)
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        html = re.sub(r"\s+", " ", html)
        if len(html) > MAX_HTML_CHARS:
            html = html[:MAX_HTML_CHARS] + "\n\n[... truncated ...]"
        return html.strip()


def ai_extract(
    html: str,
    url: str,
    prompt: str,
    model: Optional[str] = None,
    api_base: Optional[str] = None,
) -> Any:
    """
    Extract structured data from HTML using an AI model.

    Parameters
    ----------
    html:
        Raw HTML content.
    url:
        The source URL (provided as context to the AI).
    prompt:
        Natural-language instruction, e.g. "extract product name, price,
        and availability".
    model:
        LiteLLM model string (default: auto-detect from KeyManager or
        fall back to "gpt-4o-mini").
    api_base:
        Custom API base URL for local models.

    Returns
    -------
    dict or str
        Parsed JSON if the AI returns valid JSON, otherwise raw text.
    """
    try:
        import litellm
    except ImportError:
        raise ImportError(
            "AI extraction requires litellm. "
            "Install with: pip install ghost-bypass[ai]  "
            "or: pip install litellm"
        )

    cleaned = _clean_html(html)

    # Resolve model
    if not model:
        model = _auto_resolve_model(api_base)

    system_prompt = (
        "You are a data extraction assistant. Given HTML content from a webpage, "
        "extract the requested information and return it as valid JSON. "
        "Only return the JSON object/array — no markdown, no explanation, no code fences."
    )

    user_prompt = (
        f"URL: {url}\n\n"
        f"INSTRUCTION: {prompt}\n\n"
        f"HTML CONTENT:\n{cleaned}"
    )

    logger.info("[AI] Extracting with model=%s, prompt='%s'", model, prompt[:80])

    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }

    if api_base:
        kwargs["api_base"] = api_base

    try:
        response = litellm.completion(**kwargs)
        raw_text = response.choices[0].message.content.strip()

        # Try to parse as JSON
        return _parse_json_response(raw_text)
    except Exception as exc:
        logger.warning("[AI] Extraction failed: %s", exc)
        raise


def _parse_json_response(text: str) -> Any:
    """Parse AI response as JSON, handling common quirks."""
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (with optional language tag)
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from the text
    # Look for { ... } or [ ... ] patterns
    for pattern in [r"\{[\s\S]*\}", r"\[[\s\S]*\]"]:
        match = re.search(pattern, text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue

    # Return raw text if no JSON found
    return text


def _auto_resolve_model(api_base: Optional[str] = None) -> str:
    """
    Auto-resolve the best available model.

    Priority:
      1. Local model via AutoDetector (Ollama, LM Studio, etc.)
      2. KeyManager's configured providers
      3. Fallback to gpt-4o-mini
    """
    import os

    # Check for explicit env vars
    if os.environ.get("OPENAI_API_KEY"):
        return "gpt-4o-mini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude-sonnet-4-20250514"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini/gemini-2.0-flash"

    # Try KeyManager
    try:
        from ghost_bypass.ai.keys import KeyManager
        km = KeyManager()
        providers = km.list_providers()

        if "openai" in providers:
            key = km.get("openai")
            if key:
                os.environ["OPENAI_API_KEY"] = key
                return "gpt-4o-mini"

        if "anthropic" in providers:
            key = km.get("anthropic")
            if key:
                os.environ["ANTHROPIC_API_KEY"] = key
                return "claude-sonnet-4-20250514"

        if "google" in providers:
            key = km.get("google")
            if key:
                os.environ["GOOGLE_API_KEY"] = key
                return "gemini/gemini-2.0-flash"

        # Check for local endpoints
        for provider in providers:
            endpoints = km.list_endpoints(provider)
            if endpoints:
                return f"openai/{provider}"

    except Exception:
        pass

    # Try local autodetect as last resort
    try:
        from ghost_bypass.ai.autodetect import AutoDetector
        detector = AutoDetector(timeout=1.0)
        found = detector.scan(include_models=True)
        if found:
            service = found[0]
            if service["models"]:
                # For Ollama, use ollama/ prefix
                if service["name"] == "ollama":
                    return f"ollama/{service['models'][0]}"
                # For OpenAI-compatible, use openai/ prefix
                return f"openai/{service['models'][0]}"
    except Exception:
        pass

    # Default fallback
    return "gpt-4o-mini"
