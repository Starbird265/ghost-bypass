"""
ghost_bypass.ai
================
AI-powered extraction and key management.

Install with::

    pip install ghost-bypass[ai]
"""

from ghost_bypass.ai.keys import KeyManager
from ghost_bypass.ai.autodetect import AutoDetector

__all__ = ["KeyManager", "AutoDetector"]
