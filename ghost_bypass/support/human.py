#!/usr/bin/env python3
"""
ghost_bypass.support.human
============================
Human-behaviour simulator for browser automation.

Provides natural Bézier-curve mouse movements, momentum scrolling,
human-like typing, random fidget movements, and read-pause timing.

Works with Selenium WebDriver. Playwright human simulation is
handled via ``StealthConfig.simulate_human_mouse()``.
"""

import time
import random
import math
import logging
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

try:
    from selenium.webdriver.common.action_chains import ActionChains
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


class HumanBehavior:
    """
    Simulate human-like interactions in Selenium WebDriver sessions.

    All delays, movements, and scrolls include random variation to
    avoid pattern detection.
    """

    def __init__(
        self,
        min_delay: float = 0.08,
        max_delay: float = 0.45,
        movement_speed: str = "medium",
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.speed = {"slow": 1.5, "medium": 1.0, "fast": 0.6}.get(movement_speed, 1.0)

    # ── Public API ────────────────────────────────────────────────────────

    def human_click(self, driver, element, overshoot: bool = True):
        """Click an element with natural Bézier-curve mouse movement."""
        try:
            if overshoot:
                self._move_with_overshoot(driver, element)
            else:
                ActionChains(driver).move_to_element(element).perform()
            time.sleep(random.uniform(0.05, 0.18))
            element.click()
            self._pause()
        except Exception:
            try:
                element.click()
            except Exception as exc:
                logger.debug("[Human] Click fallback failed: %s", exc)

    def human_scroll(
        self,
        driver,
        direction: str = "down",
        amount: Optional[int] = None,
        smooth: bool = True,
    ):
        """Scroll with human-like momentum and deceleration."""
        amount = amount or random.randint(300, 700)
        delta = amount if direction == "down" else -amount

        if smooth:
            steps = random.randint(8, 14)
            for i in range(steps):
                progress = (i + 1) / steps
                ease = 1 - (1 - progress) ** 2
                prev = 1 - (1 - i / steps) ** 2 if i > 0 else 0
                step = int(delta * ease) - int(delta * prev)
                driver.execute_script(f"window.scrollBy(0, {step})")
                time.sleep(random.uniform(0.02, 0.07) * (1 + progress))
            self._pause(0.15)
        else:
            driver.execute_script(f"window.scrollBy(0, {delta})")
            self._pause(0.25)

    def type_like_human(self, element, text: str):
        """Type text with realistic per-character timing."""
        try:
            element.clear()
            for ch in text:
                element.send_keys(ch)
                delay = (
                    random.uniform(0.05, 0.14)
                    if ch.isalnum()
                    else random.uniform(0.09, 0.22)
                )
                time.sleep(delay)
            self._pause(0.25)
        except Exception:
            try:
                element.send_keys(text)
            except Exception:
                pass

    def read_pause(self, min_s: float = 0.5, max_s: float = 2.5):
        """Simulate a human pausing to read content."""
        time.sleep(random.uniform(min_s, max_s))

    def fidget(self, driver):
        """Perform small random mouse movements (fidgeting)."""
        if not SELENIUM_AVAILABLE:
            return
        try:
            actions = ActionChains(driver)
            for _ in range(random.randint(1, 3)):
                actions.move_by_offset(
                    random.randint(-15, 15), random.randint(-15, 15)
                )
                actions.perform()
                time.sleep(random.uniform(0.08, 0.25))
        except Exception:
            pass

    def page_view_pattern(self, driver, duration: float = 3.0):
        """Simulate realistic page-reading: scroll, pause, scroll back, etc."""
        end = time.time() + duration
        while time.time() < end:
            action = random.choices(
                ["scroll_down", "scroll_up", "pause", "fidget"],
                weights=[5, 2, 3, 1],
            )[0]
            if action == "scroll_down":
                self.human_scroll(driver, "down", smooth=True)
            elif action == "scroll_up":
                self.human_scroll(driver, "up", amount=random.randint(100, 300), smooth=True)
            elif action == "pause":
                self.read_pause(0.4, 1.2)
            elif action == "fidget":
                self.fidget(driver)
            if time.time() >= end:
                break

    # ── Private ───────────────────────────────────────────────────────────

    def _pause(self, base: Optional[float] = None):
        if base:
            time.sleep(base * random.uniform(0.8, 1.3))
        else:
            time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _move_with_overshoot(self, driver, element):
        if not SELENIUM_AVAILABLE:
            return
        try:
            actions = ActionChains(driver)
            ox = random.randint(6, 14) * random.choice([-1, 1])
            oy = random.randint(4, 10) * random.choice([-1, 1])
            actions.move_to_element_with_offset(element, ox, oy)
            actions.pause(random.uniform(0.04, 0.12))
            actions.move_to_element(element)
            actions.perform()
        except Exception:
            try:
                ActionChains(driver).move_to_element(element).perform()
            except Exception:
                pass
