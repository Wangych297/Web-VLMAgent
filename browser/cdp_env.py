"""CDP 浏览器环境 — 通过 Playwright CDP 连接用户真实 Chrome。

需要 Chrome 以远程调试模式启动：
    chrome.exe --remote-debugging-port=9222

用法：
    from browser.cdp_env import CdpBrowserEnv
    with CdpBrowserEnv(cdp_url="http://127.0.0.1:9222") as env:
        env.open_url("https://example.com")
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from browser.actions import ActionValidationError, CandidateElement, get_candidate_by_id, validate_action


class CdpBrowserEnv:
    """通过 Playwright connect_over_cdp 操控用户真实 Chrome。

    Chrome 需以调试模式启动：
        chrome.exe --remote-debugging-port=9222
    """

    def __init__(
        self,
        headless: bool = True,
        cdp_url: str = "http://127.0.0.1:9222",
        viewport: dict[str, int] | None = None,
        timeout_ms: int = 30_000,
    ) -> None:
        self.headless = headless
        self.cdp_url = cdp_url
        self.viewport = viewport or {"width": 1280, "height": 900}
        self.timeout_ms = timeout_ms
        self._playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "CdpBrowserEnv":
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        # 使用默认 context（保留用户登录态）
        self.context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context(viewport=self.viewport)
        self.page = self.context.new_page() if self.context.pages else self.context.pages[0]
        self.page.set_default_timeout(self.timeout_ms)
        return self

    def __exit__(self, exc_type: object, exc_value: object, exc_traceback: object) -> None:
        if self.page:
            try:
                self.page.close()
            except Exception:
                pass
        if self._playwright:
            self._playwright.stop()

    # ---------- 浏览器操作 ----------

    def open_url(self, url: str) -> None:
        page = self._require_page()
        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass

    def screenshot(self, path: Path) -> None:
        page = self._require_page()
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=False, timeout=30_000)

    def accessibility_snapshot(self) -> str:
        page = self._require_page()
        try:
            return page.aria_snapshot(mode="ai", boxes=True, timeout=self.timeout_ms)
        except Exception:
            return ""

    def candidates(self) -> list[CandidateElement]:
        from browser.dom_extractor import extract_candidates
        return extract_candidates(self._require_page())

    def page_text(self, candidates: list[CandidateElement] | None = None) -> str:
        page = self._require_page()
        candidate_text = "\n".join(candidate.text for candidate in candidates or [] if candidate.text)
        try:
            body_text = page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            body_text = ""
        return f"{body_text}\n{candidate_text}".strip()

    def current_url(self) -> str:
        return self._require_page().url

    def execute_action(
        self,
        action_data: dict[str, Any],
        candidates: list[CandidateElement],
    ) -> dict[str, Any]:
        page = self._require_page()
        result: dict[str, Any] = {
            "ok": False,
            "action": action_data,
            "url_before": page.url,
            "url_after": page.url,
            "error_type": None,
            "error": None,
            "traceback": None,
            "browser_backend": "cdp",
        }

        try:
            action = validate_action(action_data, candidates)

            if action.action == "click":
                candidate = get_candidate_by_id(candidates, action.target_id or -1)
                page.locator(candidate.selector).first.click(timeout=self.timeout_ms)
                self._settle_page()
            elif action.action == "type":
                candidate = get_candidate_by_id(candidates, action.target_id or -1)
                locator = page.locator(candidate.selector).first
                if candidate.tag == "select":
                    self._select_option(locator, action.text)
                else:
                    locator.fill(action.text, timeout=self.timeout_ms)
                    if action.key.strip():
                        page.keyboard.press(action.key.strip())
                self._settle_page()
            elif action.action == "scroll":
                delta = 700 if action.direction == "down" else -700
                page.evaluate(f"window.scrollBy(0, {delta})")
            elif action.action == "press":
                page.keyboard.press(action.key)
            elif action.action == "back":
                page.go_back(wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._settle_page()
            elif action.action == "navigate":
                page.goto(action.text.strip(), wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._settle_page()
            elif action.action == "search":
                page.goto(
                    f"https://www.bing.com/search?q={quote_plus(action.text.strip())}",
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                self._settle_page()
            elif action.action == "wait":
                import time

                time.sleep(1)
            elif action.action in ("answer", "done", "ask_user"):
                pass

            result.update({"ok": True, "success": True, "action": action.model_dump(), "url_after": page.url})
        except ActionValidationError as exc:
            result.update(
                {
                    "success": False,
                    "error": str(exc),
                    "error_type": "validation_error",
                    "traceback": traceback.format_exc(),
                    "url_after": page.url,
                }
            )
        except Exception as exc:
            result.update(
                {
                    "success": False,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                    "traceback": traceback.format_exc(),
                    "url_after": page.url,
                }
            )
        return result

    def _require_page(self) -> Page:
        if not self.page:
            raise RuntimeError("CDP page not initialized")
        return self.page

    def _settle_page(self) -> None:
        page = self._require_page()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3_000)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=3_000)
        except Exception:
            pass

    def _select_option(self, locator: Any, text: str) -> None:
        try:
            locator.select_option(label=text, timeout=self.timeout_ms)
            return
        except Exception:
            pass
        try:
            locator.select_option(value=text, timeout=self.timeout_ms)
            return
        except Exception:
            pass
        locator.select_option(index=0, timeout=self.timeout_ms)

