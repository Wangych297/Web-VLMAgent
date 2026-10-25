from __future__ import annotations

from playwright.sync_api import Page

from browser.actions import CandidateElement


CANDIDATE_SELECTOR = ",".join(
    [
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
    ]
)


def extract_candidates(page: Page, max_candidates: int = 320) -> list[CandidateElement]:
    raw_candidates = page.evaluate(
        """
        ({ candidateSelector, maxCandidates }) => {
            document
                .querySelectorAll("[data-web-mmagent-id]")
                .forEach((element) => element.removeAttribute("data-web-mmagent-id"));

            const elements = Array.from(document.querySelectorAll(candidateSelector));

            function inferRole(element) {
                const explicitRole = element.getAttribute("role");
                if (explicitRole) {
                    return explicitRole;
                }

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
                    .replace(/\\s+/g, " ")
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
                if (["button", "a", "input", "textarea", "select", "summary"].includes(tag)) {
                    return true;
                }
                if (
                    element.getAttribute("onclick") ||
                    element.getAttribute("role") ||
                    element.getAttribute("contenteditable") === "true" ||
                    (element.getAttribute("tabindex") && element.getAttribute("tabindex") !== "-1")
                ) {
                    return true;
                }
                return false;
            }

            function hasPointerAncestor(element) {
                let parent = element.parentElement;
                while (parent && parent !== document.body) {
                    if (window.getComputedStyle(parent).cursor === "pointer") {
                        return true;
                    }
                    parent = parent.parentElement;
                }
                return false;
            }

            function hasOwnInteractiveSignal(element) {
                if (hasSemanticInteractiveSignal(element)) {
                    return true;
                }
                const style = window.getComputedStyle(element);
                if (style.cursor === "pointer") {
                    return !hasPointerAncestor(element);
                }
                return false;
            }

            function interactiveAncestor(element) {
                return element.closest(
                    "a,button,input,textarea,select,summary,[onclick],[role=button],[role=link],[role=textbox],[role=searchbox],[role=combobox],[role=checkbox],[role=radio],[role=tab],[role=menuitem],[role=option],[tabindex]:not([tabindex='-1']),[contenteditable=true]"
                );
            }

            function isCandidateElement(element) {
                if (hasOwnInteractiveSignal(element)) {
                    return true;
                }

                const text = textFor(element);
                if (!text || text.length > 80) {
                    return false;
                }
                const clickableAncestor = interactiveAncestor(element);
                return Boolean(clickableAncestor && clickableAncestor !== document.body);
            }

            function intersectsViewport(rect) {
                return (
                    rect.right > 0 &&
                    rect.bottom > 0 &&
                    rect.left < window.innerWidth &&
                    rect.top < window.innerHeight
                );
            }

            function isTopmostAtPoint(element, x, y) {
                if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) {
                    return false;
                }
                const top = document.elementFromPoint(x, y);
                return Boolean(top && (top === element || element.contains(top) || top.contains(element)));
            }

            function isCoveredInViewport(element, rect) {
                if (!intersectsViewport(rect)) {
                    return false;
                }
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
                if (!isCandidateElement(element)) {
                    continue;
                }
                if (!hasOwnInteractiveSignal(element)) {
                    const ancestor = interactiveAncestor(element);
                    if (ancestor && ancestor !== element && ancestor !== document.body) {
                        continue;
                    }
                }
                if (seenElements.has(element)) {
                    continue;
                }
                const rect = element.getBoundingClientRect();
                const visible = isVisible(element, rect);
                if (!visible) {
                    continue;
                }

                if (isCoveredInViewport(element, rect)) {
                    continue;
                }

                seenElements.add(element);
                rawCandidates.push({ element, rect, visible });
            }

            rawCandidates.sort((a, b) => {
                const aInViewport = intersectsViewport(a.rect) ? 0 : 1;
                const bInViewport = intersectsViewport(b.rect) ? 0 : 1;
                if (aInViewport !== bInViewport) {
                    return aInViewport - bInViewport;
                }
                if (Math.abs(a.rect.y - b.rect.y) > 1) {
                    return a.rect.y - b.rect.y;
                }
                return a.rect.x - b.rect.x;
            });

            const candidates = [];
            for (const item of rawCandidates.slice(0, maxCandidates)) {
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
                    bbox: {
                        x: rect.x,
                        y: rect.y,
                        width: rect.width,
                        height: rect.height,
                    },
                    visible,
                    enabled: isEnabled(element),
                });
            }

            return candidates;
        }
        """,
        {"candidateSelector": CANDIDATE_SELECTOR, "maxCandidates": max_candidates},
    )
    return [CandidateElement.model_validate(candidate) for candidate in raw_candidates]
