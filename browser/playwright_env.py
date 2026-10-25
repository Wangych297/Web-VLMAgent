from __future__ import annotations

import traceback
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

from playwright.sync_api import Browser, BrowserContext, Dialog, Page, Playwright, TimeoutError, sync_playwright

from browser.actions import ActionValidationError, CandidateElement, get_candidate_by_id, validate_action
from browser.dom_extractor import extract_candidates


DANGEROUS_DIALOG_TERMS = (
    "delete",
    "remove",
    "purchase",
    "payment",
    "checkout",
    "submit",
    "sign",
    "pay",
)


class PlaywrightEnv:
    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 30_000,
        viewport: dict[str, int] | None = None,
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.viewport = viewport or {"width": 1280, "height": 900}
        self._playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "PlaywrightEnv":
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        )
        self.context = self.browser.new_context(
            viewport=self.viewport,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        )
        self.page = self.context.new_page()
        # 隐藏自动化特征
        self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            delete navigator.__proto__.webdriver;
        """)
        self.page.set_default_timeout(self.timeout_ms)
        return self

    def __exit__(self, exc_type: object, exc_value: object, exc_traceback: object) -> None:
        if self.context is not None:
            self.context.close()
        if self.browser is not None:
            self.browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def open_url(self, url: str) -> None:
        page = self._require_page()
        page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=5_000)
        except TimeoutError:
            pass

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._with_navigation_retry(lambda page: page.screenshot(path=str(path), full_page=False, timeout=30_000))

    def screenshot_bytes(self) -> bytes:
        return self._with_navigation_retry(lambda page: page.screenshot(full_page=False, timeout=30_000))

    def accessibility_snapshot(self) -> str:
        return self._with_navigation_retry(lambda page: page.aria_snapshot(mode="ai", boxes=True, timeout=self.timeout_ms))

    def candidates(self) -> list[CandidateElement]:
        return self._with_navigation_retry(lambda page: extract_candidates(page))

    def page_text(self, candidates: list[CandidateElement]) -> str:
        candidate_text = "\n".join(candidate.text for candidate in candidates if candidate.text)
        try:
            body_text = self._with_navigation_retry(lambda page: page.evaluate("() => document.body ? document.body.innerText : ''"))
        except Exception:
            body_text = ""
        return f"{body_text}\n{candidate_text}".strip()

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
        }

        try:
            action = validate_action(action_data, candidates)

            if action.action == "click":
                target = get_candidate_by_id(candidates, action.target_id or -1)
                locator = page.locator(target.selector).first
                href = _safe_get_href(locator) if target.tag == "a" else ""
                click_url_before = page.url
                dialog_events: list[dict[str, Any]] = []
                dialog_handler = lambda dialog: self._handle_dialog(dialog, dialog_events)
                page.once("dialog", dialog_handler)
                try:
                    popup_page = None
                    try:
                        with page.expect_popup(timeout=2_000) as popup_info:
                            locator.click(timeout=self.timeout_ms)
                        popup_page = popup_info.value
                    except TimeoutError:
                        popup_page = None

                    if popup_page is not None:
                        self.page = popup_page
                        page = popup_page
                        page.set_default_timeout(self.timeout_ms)
                    self._settle_page()
                    if page.url == click_url_before and _is_navigable_href(href):
                        fallback_url = urljoin(click_url_before, href)
                        page.goto(fallback_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                        result["link_navigation_fallback"] = fallback_url
                        self._settle_page()
                    if _should_scroll_to_top_after_click(target):
                        page.evaluate("() => window.scrollTo(0, 0)")
                        page.wait_for_timeout(150)
                finally:
                    try:
                        page.remove_listener("dialog", dialog_handler)
                    except Exception:
                        pass
                if dialog_events:
                    result["dialogs"] = dialog_events
            elif action.action == "type":
                target = get_candidate_by_id(candidates, action.target_id or -1)
                locator = page.locator(target.selector).first
                if target.tag == "select":
                    self._select_option(locator, action.text)
                    self._settle_page()
                else:
                    input_type = ""
                    if target.tag == "input":
                        input_type = (locator.get_attribute("type", timeout=self.timeout_ms) or "text").lower()
                    locator.fill(_normalize_temporal_input(action.text, input_type), timeout=self.timeout_ms)
                    if action.key.strip():
                        page.keyboard.press(action.key.strip())
                        self._settle_page()
            elif action.action == "scroll":
                delta_y = 700 if action.direction == "down" else -700
                page.mouse.wheel(0, delta_y)
            elif action.action == "press":
                page.keyboard.press(action.key)
            elif action.action == "back":
                page.go_back(wait_until="domcontentloaded", timeout=self.timeout_ms)
                self._settle_page()
            elif action.action == "navigate":
                self.open_url(action.text.strip())
            elif action.action == "search":
                self.open_url(f"https://www.bing.com/search?q={quote_plus(action.text.strip())}")
            elif action.action == "wait":
                page.wait_for_timeout(1_000)
            elif action.action == "answer":
                pass
            elif action.action == "done":
                pass
            elif action.action == "ask_user":
                pass

            result.update(
                {
                    "ok": True,
                    "action": action.model_dump(),
                    "url_after": page.url,
                }
            )
        except ActionValidationError as exc:
            result.update(
                {
                    "error_type": "validation_error",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        except Exception as exc:
            result.update(
                {
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "url_after": page.url,
                }
            )

        return result

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

    def _with_navigation_retry(self, operation: Any, attempts: int = 3) -> Any:
        last_error: Exception | None = None
        for attempt in range(attempts):
            page = self._require_page()
            try:
                if attempt:
                    self._settle_page()
                return operation(page)
            except Exception as exc:
                last_error = exc
                if not _is_transient_navigation_error(exc) or attempt >= attempts - 1:
                    raise
                self._settle_page()
                try:
                    page.wait_for_timeout(250 * (attempt + 1))
                except Exception:
                    pass
        if last_error:
            raise last_error
        raise RuntimeError("operation failed without an exception")

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("Playwright page is not initialized")
        return self.page

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

    def _handle_dialog(self, dialog: Dialog, events: list[dict[str, Any]]) -> None:
        message = dialog.message
        lowered = message.lower()
        dangerous = any(term in lowered for term in DANGEROUS_DIALOG_TERMS)
        events.append(
            {
                "type": dialog.type,
                "message": message,
                "accepted": not dangerous,
            }
        )
        if dangerous:
            dialog.dismiss()
        else:
            dialog.accept()


def _normalize_temporal_input(text: str, input_type: str) -> str:
    value = text.strip()
    if input_type == "date":
        return _normalize_date(value)
    if input_type == "month":
        return _normalize_month(value)
    return text


def _normalize_date(value: str) -> str:
    iso_match = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if iso_match:
        year, month, day = iso_match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    slash_match = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", value)
    if slash_match:
        first, second, year = slash_match.groups()
        # Many local forms display dd/MM/yyyy even though the native input expects ISO.
        day, month = int(first), int(second)
        return f"{int(year):04d}-{month:02d}-{day:02d}"

    return value


def _normalize_month(value: str) -> str:
    iso_match = re.fullmatch(r"(\d{4})[-/](\d{1,2})", value)
    if iso_match:
        year, month = iso_match.groups()
        return f"{int(year):04d}-{int(month):02d}"

    slash_match = re.fullmatch(r"(\d{1,2})[-/](\d{4})", value)
    if slash_match:
        month, year = slash_match.groups()
        return f"{int(year):04d}-{int(month):02d}"

    return value


def _should_scroll_to_top_after_click(target: CandidateElement) -> bool:
    text = " ".join([target.role, target.text]).lower()
    navigation_terms = (
        "next",
        "previous",
        "eligibility check",
        "key information",
        "applicants",
        "research budgets",
        "budgets & incomes",
    )
    return any(term in text for term in navigation_terms)


def _safe_get_href(locator: Any) -> str:
    try:
        return locator.get_attribute("href", timeout=2_000) or ""
    except Exception:
        return ""


def _is_navigable_href(href: str) -> bool:
    value = href.strip()
    if not value or value.startswith("#"):
        return False
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https", "file"}:
        return False
    return not value.lower().startswith(("javascript:", "mailto:", "tel:"))


def _is_transient_navigation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    transient_terms = (
        "execution context was destroyed",
        "most likely because of a navigation",
        "cannot find context with specified id",
        "frame was detached",
    )
    return any(term in message for term in transient_terms)
