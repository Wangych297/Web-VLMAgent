from __future__ import annotations

import json
import subprocess
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from browser.actions import ActionValidationError, CandidateElement, get_candidate_by_id, validate_action


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ACCESS_ROOT = PROJECT_ROOT / "browser" / "web_access_runtime"
CHECK_DEPS_SCRIPT = WEB_ACCESS_ROOT / "scripts" / "check-deps.mjs"


class WebAccessEnv:
    """Browser environment backed by the bundled web-access CDP proxy.

    The proxy runs at http://127.0.0.1:3456 and controls a real Chrome/Edge tab
    through CDP. This backend is intended for real websites that block fresh
    Playwright contexts or require the user's existing browser session.
    """

    page = None

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 30_000,
        proxy_url: str = "http://127.0.0.1:3456",
        browser: str | None = None,
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.proxy_url = proxy_url.rstrip("/")
        self.browser = browser
        self.target_id: str | None = None

    def __enter__(self) -> "WebAccessEnv":
        self._ensure_proxy()
        return self

    def __exit__(self, exc_type: object, exc_value: object, exc_traceback: object) -> None:
        if self.target_id:
            try:
                self._request_json("GET", "/close", query={"target": self.target_id})
            except Exception:
                pass

    def open_url(self, url: str) -> None:
        if self.target_id is None:
            payload = self._request_json("POST", "/new", data=url)
            target_id = payload.get("targetId")
            if not isinstance(target_id, str) or not target_id:
                raise RuntimeError(f"web-access did not return targetId: {payload}")
            self.target_id = target_id
            return
        self._request_json("POST", "/navigate", query={"target": self.target_id}, data=url)

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        query = {"target": self._require_target(), "file": str(path.resolve())}
        try:
            self._request_json("GET", "/screenshot", query=query)
        except Exception:
            time.sleep(1.0)
            self._request_json("GET", "/screenshot", query=query)

    def screenshot_bytes(self) -> bytes:
        query = {"target": self._require_target()}
        try:
            return self._request_bytes("GET", "/screenshot", query=query)
        except Exception:
            time.sleep(1.0)
            return self._request_bytes("GET", "/screenshot", query=query)

    def accessibility_snapshot(self) -> str:
        try:
            payload = self._request_json("GET", "/accessibility", query={"target": self._require_target()})
        except Exception:
            return self._dom_accessibility_summary()
        nodes = payload.get("nodes", payload)
        if not isinstance(nodes, list):
            return ""
        lines: list[str] = []
        for node in nodes[:500]:
            if not isinstance(node, dict):
                continue
            role = _ax_value(node.get("role"))
            name = _ax_value(node.get("name"))
            value = _ax_value(node.get("value"))
            ignored = node.get("ignored")
            if ignored or not (role or name or value):
                continue
            pieces = [piece for piece in [role, name, value] if piece]
            lines.append(" | ".join(pieces))
        return "\n".join(lines)

    def candidates(self) -> list[CandidateElement]:
        raw = self._eval_json(_CANDIDATE_EXTRACTION_JS)
        if not isinstance(raw, list):
            return []
        return [CandidateElement.model_validate(candidate) for candidate in raw]

    def page_text(self, candidates: list[CandidateElement] | None = None) -> str:
        try:
            body_text = self._eval(
                "() => document.body ? document.body.innerText : ''",
                as_function=True,
            )
        except Exception:
            body_text = ""
        candidate_text = "\n".join(candidate.text for candidate in candidates or [] if candidate.text)
        return f"{body_text or ''}\n{candidate_text}".strip()

    def current_url(self) -> str:
        payload = self._request_json("GET", "/info", query={"target": self._require_target()})
        url = payload.get("url")
        return str(url) if url else ""

    def execute_action(
        self,
        action_data: dict[str, Any],
        candidates: list[CandidateElement],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "action": action_data,
            "url_before": _safe_call(self.current_url, ""),
            "url_after": _safe_call(self.current_url, ""),
            "error_type": None,
            "error": None,
            "traceback": None,
            "browser_backend": "web_access",
        }

        try:
            action = validate_action(action_data, candidates)

            if action.action == "click":
                target = get_candidate_by_id(candidates, action.target_id or -1)
                click_result = self._request_json(
                    "POST",
                    "/clickAt",
                    query={"target": self._require_target()},
                    data=target.selector,
                )
                if click_result.get("error"):
                    raise RuntimeError(str(click_result["error"]))
                time.sleep(0.6)
            elif action.action == "type":
                target = get_candidate_by_id(candidates, action.target_id or -1)
                self._fill(target, action.text)
                time.sleep(0.2)
            elif action.action == "scroll":
                self._request_json(
                    "GET",
                    "/scroll",
                    query={
                        "target": self._require_target(),
                        "direction": action.direction,
                        "y": "700",
                    },
                )
            elif action.action == "press":
                self._press(action.key)
                time.sleep(0.2)
            elif action.action == "back":
                self._request_json("GET", "/back", query={"target": self._require_target()})
            elif action.action == "navigate":
                self.open_url(action.text.strip())
                time.sleep(0.6)
            elif action.action == "search":
                query = urllib.parse.quote_plus(action.text.strip())
                self.open_url(f"https://www.bing.com/search?q={query}")
                time.sleep(0.6)
            elif action.action == "wait":
                time.sleep(1.0)
            elif action.action in {"answer", "done", "ask_user"}:
                pass

            result.update(
                {
                    "ok": True,
                    "action": action.model_dump(),
                    "url_after": _safe_call(self.current_url, result["url_before"]),
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
                    "url_after": _safe_call(self.current_url, result["url_before"]),
                }
            )
        return result

    def _ensure_proxy(self) -> None:
        if not CHECK_DEPS_SCRIPT.exists():
            raise RuntimeError(f"web-access check script not found: {CHECK_DEPS_SCRIPT}")
        command = ["node", str(CHECK_DEPS_SCRIPT)]
        if self.browser:
            command.extend(["--browser", self.browser])
        try:
            completed = subprocess.run(
                command,
                cwd=str(WEB_ACCESS_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(45, self.timeout_ms // 1000 + 15),
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Node.js is required for web-access but 'node' was not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("web-access check-deps timed out; check Chrome/Edge remote debugging") from exc
        if completed.returncode != 0:
            output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
            raise RuntimeError(f"web-access is not ready:\n{output}")

    def _fill(self, target: CandidateElement, text: str) -> None:
        script = f"""
        (() => {{
            const el = document.querySelector({json.dumps(target.selector)});
            const text = {json.dumps(text)};
            if (!el) return {{ error: 'element not found' }};
            el.scrollIntoView({{ block: 'center', inline: 'center' }});
            el.focus();
            const tag = el.tagName.toLowerCase();
            if (tag === 'select') {{
                const options = Array.from(el.options || []);
                const wanted = text.trim().toLowerCase();
                const option = options.find((item) =>
                    item.value.toLowerCase() === wanted ||
                    item.textContent.trim().toLowerCase() === wanted
                ) || options[0];
                if (option) el.value = option.value;
            }} else if ('value' in el) {{
                el.value = text;
            }} else {{
                el.textContent = text;
            }}
            el.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: text }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return {{ filled: true, tag }};
        }})()
        """
        result = self._eval(script)
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(str(result["error"]))

    def _press(self, key: str) -> None:
        script = f"""
        (() => {{
            const key = {json.dumps(key)};
            const el = document.activeElement || document.body;
            const eventInit = {{ key, code: key, bubbles: true, cancelable: true }};
            el.dispatchEvent(new KeyboardEvent('keydown', eventInit));
            el.dispatchEvent(new KeyboardEvent('keyup', eventInit));
            if (key === 'Enter' && el && el.form && typeof el.form.requestSubmit === 'function') {{
                el.form.requestSubmit();
            }}
            return {{ pressed: key }};
        }})()
        """
        self._eval(script)

    def _dom_accessibility_summary(self) -> str:
        try:
            rows = self._eval_json(_DOM_ACCESSIBILITY_JS)
        except Exception:
            return ""
        if not isinstance(rows, list):
            return ""
        lines = []
        for row in rows[:300]:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "")
            name = str(row.get("name") or "")
            tag = str(row.get("tag") or "")
            if role or name:
                lines.append(" | ".join(piece for piece in [role or tag, name] if piece))
        return "\n".join(lines)

    def _eval_json(self, expression: str) -> Any:
        value = self._eval(expression)
        if isinstance(value, str):
            return json.loads(value)
        return value

    def _eval(self, expression: str, as_function: bool = False) -> Any:
        body = f"({expression})()" if as_function else expression
        payload = self._request_json("POST", "/eval", query={"target": self._require_target()}, data=body)
        if "error" in payload:
            raise RuntimeError(str(payload["error"]))
        return payload.get("value")

    def _request_json(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        data: str | None = None,
    ) -> dict[str, Any]:
        body = self._request_bytes(method, path, query=query, data=data)
        if not body:
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"web-access returned invalid JSON from {path}: {body[:200]!r}") from exc
        return payload if isinstance(payload, dict) else {"value": payload}

    def _request_bytes(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        data: str | None = None,
    ) -> bytes:
        query_string = urllib.parse.urlencode(query or {})
        url = f"{self.proxy_url}{path}"
        if query_string:
            url = f"{url}?{query_string}"
        encoded = data.encode("utf-8") if data is not None else None
        request = urllib.request.Request(url=url, data=encoded, method=method)
        if data is not None:
            request.add_header("Content-Type", "text/plain; charset=utf-8")
        try:
            with urllib.request.urlopen(request, timeout=max(10, self.timeout_ms / 1000 + 15)) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"web-access {method} {path} failed with HTTP {exc.code}: {body[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"web-access {method} {path} failed: {exc.reason}") from exc

    def _require_target(self) -> str:
        if not self.target_id:
            raise RuntimeError("web-access target is not initialized")
        return self.target_id


def _ax_value(payload: Any) -> str:
    if isinstance(payload, dict):
        value = payload.get("value")
        return "" if value is None else str(value)
    return "" if payload is None else str(payload)


def _safe_call(func: Any, fallback: str) -> str:
    try:
        value = func()
    except Exception:
        return fallback
    return value or fallback


_CANDIDATE_EXTRACTION_JS = r"""
JSON.stringify((() => {
    document
        .querySelectorAll("[data-web-mmagent-id]")
        .forEach((element) => element.removeAttribute("data-web-mmagent-id"));

    const candidateSelector = [
        "button",
        "a",
        "input",
        "textarea",
        "select",
        "[onclick]",
        "[role=button]",
        "[role=link]",
        "[role=textbox]",
        "[role=searchbox]",
        "[role=combobox]",
        "[role=checkbox]",
        "[role=radio]",
        "[role=tab]",
        "[role=menuitem]",
        "[role=option]",
        "[tabindex]:not([tabindex='-1'])",
        "[contenteditable=true]",
        "summary",
        "span",
        "div",
        "li",
    ].join(",");
    const elements = Array.from(document.querySelectorAll(candidateSelector));

    function inferRole(element) {
        const explicitRole = element.getAttribute("role");
        if (explicitRole) return explicitRole;
        const tag = element.tagName.toLowerCase();
        const inputType = (element.getAttribute("type") || "text").toLowerCase();
        if (tag === "button") return "button";
        if (tag === "summary") return "button";
        if (tag === "a") return "link";
        if (tag === "textarea") return "textbox";
        if (tag === "select") return "combobox";
        if (element.getAttribute("contenteditable") === "true") return "textbox";
        if (tag === "input") {
            if (["button", "submit", "reset"].includes(inputType)) return "button";
            if (inputType === "radio") return "radio";
            if (inputType === "checkbox") return "checkbox";
            return "textbox";
        }
        if (element.getAttribute("onclick")) return "button";
        if (window.getComputedStyle(element).cursor === "pointer") return "button";
        return "";
    }

    function textFor(element) {
        const tag = element.tagName.toLowerCase();
        const labelText = element.labels
            ? Array.from(element.labels).map((label) => label.innerText).join(" ")
            : "";
        const enclosingLabel = element.closest("label");
        const pieces = [
            element.innerText,
            labelText,
            enclosingLabel ? enclosingLabel.innerText : "",
            tag === "input" || tag === "textarea" ? element.value : "",
            element.getAttribute("aria-label"),
            element.getAttribute("placeholder"),
            element.getAttribute("title"),
            element.getAttribute("alt"),
        ];
        return pieces
            .filter(Boolean)
            .join(" ")
            .replace(/\s+/g, " ")
            .trim()
            .slice(0, 200);
    }

    function isVisible(element, rect) {
        const style = window.getComputedStyle(element);
        return (
            rect.width > 0 &&
            rect.height > 0 &&
            style.display !== "none" &&
            style.visibility !== "hidden" &&
            Number(style.opacity || "1") > 0
        );
    }

    function isEnabled(element) {
        return !element.disabled && element.getAttribute("aria-disabled") !== "true";
    }

    function hasSemanticInteractiveSignal(element) {
        const tag = element.tagName.toLowerCase();
        if (["button", "a", "input", "textarea", "select", "summary"].includes(tag)) return true;
        if (
            element.getAttribute("onclick") ||
            element.getAttribute("role") ||
            element.getAttribute("contenteditable") === "true" ||
            (element.getAttribute("tabindex") && element.getAttribute("tabindex") !== "-1")
        ) return true;
        return false;
    }

    function hasPointerAncestor(element) {
        let parent = element.parentElement;
        while (parent && parent !== document.body) {
            if (window.getComputedStyle(parent).cursor === "pointer") return true;
            parent = parent.parentElement;
        }
        return false;
    }

    function hasOwnInteractiveSignal(element) {
        if (hasSemanticInteractiveSignal(element)) return true;
        const style = window.getComputedStyle(element);
        if (style.cursor === "pointer") return !hasPointerAncestor(element);
        return false;
    }

    function interactiveAncestor(element) {
        return element.closest(
            "a,button,input,textarea,select,summary,[onclick],[role=button],[role=link],[role=textbox],[role=searchbox],[role=combobox],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=option],[tabindex]:not([tabindex='-1']),[contenteditable=true]"
        );
    }

    function isCandidateElement(element) {
        if (hasOwnInteractiveSignal(element)) return true;

        const text = textFor(element);
        if (!text || text.length > 80) return false;
        const clickableAncestor = interactiveAncestor(element);
        return Boolean(clickableAncestor && clickableAncestor !== document.body);
    }

    function intersectsViewport(rect) {
        return rect.right > 0 && rect.bottom > 0 && rect.left < window.innerWidth && rect.top < window.innerHeight;
    }

    function isTopmostAtPoint(element, x, y) {
        if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) return false;
        const top = document.elementFromPoint(x, y);
        return Boolean(top && (top === element || element.contains(top) || top.contains(element)));
    }

    function isCoveredInViewport(element, rect) {
        if (!intersectsViewport(rect)) return false;
        const left = Math.max(0, rect.left);
        const right = Math.min(window.innerWidth - 1, rect.right);
        const top = Math.max(0, rect.top);
        const bottom = Math.min(window.innerHeight - 1, rect.bottom);
        const points = [
            [(left + right) / 2, (top + bottom) / 2],
            [left + 1, top + 1],
            [right - 1, top + 1],
            [left + 1, bottom - 1],
            [right - 1, bottom - 1],
        ];
        return !points.some(([x, y]) => isTopmostAtPoint(element, x, y));
    }

    const rawCandidates = [];
    const seenElements = new Set();
    for (const element of elements) {
        if (!isCandidateElement(element)) continue;
        if (!hasOwnInteractiveSignal(element)) {
            const ancestor = interactiveAncestor(element);
            if (ancestor && ancestor !== element && ancestor !== document.body) continue;
        }
        if (seenElements.has(element)) continue;
        const rect = element.getBoundingClientRect();
        const visible = isVisible(element, rect);
        if (!visible) continue;
        if (isCoveredInViewport(element, rect)) continue;
        seenElements.add(element);
        rawCandidates.push({ element, rect, visible });
    }

    rawCandidates.sort((a, b) => {
        const aInViewport = intersectsViewport(a.rect) ? 0 : 1;
        const bInViewport = intersectsViewport(b.rect) ? 0 : 1;
        if (aInViewport !== bInViewport) return aInViewport - bInViewport;
        if (Math.abs(a.rect.y - b.rect.y) > 1) return a.rect.y - b.rect.y;
        return a.rect.x - b.rect.x;
    });

    const candidates = [];
    for (const item of rawCandidates.slice(0, 320)) {
        const element = item.element;
        const rect = item.rect;
        const visible = item.visible;
        const elementId = candidates.length + 1;
        element.setAttribute("data-web-mmagent-id", String(elementId));
        candidates.push({
            element_id: elementId,
            tag: element.tagName.toLowerCase(),
            role: inferRole(element),
            text: textFor(element),
            selector: `[data-web-mmagent-id="${elementId}"]`,
            bbox: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
            visible,
            enabled: isEnabled(element),
        });
    }
    return candidates;
})())
"""


_DOM_ACCESSIBILITY_JS = r"""
JSON.stringify((() => {
    const selector = [
        "h1", "h2", "h3", "button", "a", "input", "textarea", "select",
        "[role]", "[aria-label]", "[placeholder]", "[title]"
    ].join(",");
    return Array.from(document.querySelectorAll(selector)).slice(0, 300).map((element) => {
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        const visible = rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
        const tag = element.tagName.toLowerCase();
        const role = element.getAttribute("role") || "";
        const name = [
            element.getAttribute("aria-label"),
            element.getAttribute("placeholder"),
            element.getAttribute("title"),
            element.innerText,
            element.value,
        ].filter(Boolean).join(" ").replace(/\s+/g, " ").trim().slice(0, 200);
        return { tag, role, name, visible };
    }).filter((item) => item.visible && (item.role || item.name));
})())
"""
