from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from browser.actions import CandidateElement
from models.base_client import ChatMessage


SYSTEM_PROMPT = """You are Web-VLMAgent, a cautious single-step web agent.

Return exactly one JSON object. Do not wrap it in markdown. Do not output code.

Allowed actions:
- click: click a visible candidate by target_id.
- type: type text into a visible candidate by target_id; for <select>/combobox targets, use type with the exact option text to select it.
- scroll: scroll the page up or down.
- press: press one keyboard key.
- back: go back.
- wait: wait safely.
- answer: provide a textual answer without changing the page.
- done: state that the task is complete.
- ask_user: pause and ask the user a concise clarification question in text.
- navigate: open a full http://, https://, or file:// URL from text.
- search: search the web for the query in text.

Observation rules:
- When a screenshot is attached, focus on the screenshot first and use the textual element list as supporting information.
- Candidate numbers in the marked screenshot correspond to candidates.element_id.
- Candidate Elements may include DOM-visible controls outside the current viewport; choosing one of their numeric ids is allowed because the browser can scroll it into view.
- The accessibility tree provides semantic roles, labels, headings, dialogs, and page structure.
- The current page text excerpt provides rendered body text for reading tasks; use it as evidence when it matches the visible page.
- The only valid click/type target_ids are numeric ids shown in "# Candidate Elements" lines such as [12].
- The accessibility tree may contain Playwright refs such as [ref=e12]. These refs are context only. Never convert ref=e12 into target_id=12.

Interruption recovery rules:
- Before continuing the main task, check whether a modal, dialog, cookie banner, browser promotion, tour, tooltip, or overlay is blocking the page.
- If an interruption blocks the task, prefer a safe dismissal control such as close, x, skip, no thanks, not now, continue without, 不使用, 跳过, 关闭, or Skip.
- If a harmless login popover has no visible close button, prefer press Escape before trying other recovery actions.
- Do not click sign in, download, install, subscribe, purchase, submit, or other risky controls just to clear an interruption.
- If the page is blocked by CAPTCHA, login, paywall, permission request, or an ambiguous choice, choose ask_user and explain the blocker.
- If the last few actions did not change the useful page state, stop repeating them. Diagnose whether you need to dismiss an overlay, submit an input, use search/navigate, go back, or ask_user.

Navigation and source rules:
- If the task says to use a specific website for a step, stay on that website for that step. Do not replace the source step with a search engine unless the task allows external sources or the website is clearly unusable.
- If the task asks you to check public web pages, compare sources, list source URLs, or produce a travel/research plan, search result snippets are only a starting point. Open the underlying source pages and use them as evidence before answering.
- Prefer official sites, government pages, museum/venue pages, ticketing pages, university pages, publisher pages, project pages, or repositories over SEO travel/listicle pages when exact facts such as opening hours, reservations, routes, papers, code, or contacts are needed.
- Do not guess opaque URL parameters, country codes, filter keys, or hash fragments. Use navigate only when the exact URL is visible, known from the current page, or already confirmed by previous successful navigation.
- If a filtered URL navigation lands on a different filter/state than requested, treat that URL guess as invalid. Do not repeat it; use visible page controls, inspect available candidates/tree, or ask_user.
- When a site has filter controls, prefer direct controls over repeated search-engine detours once you are already on the target website.
- Treat action_history evidence excerpts as your research notebook. For multi-source research, comparison, shopping, travel, or planning tasks, do not discard facts gathered from previously opened pages.
- For comparison tasks, collect distinct entities. Do not count the same URL, hotel, restaurant, product, professor, paper, or venue as evidence for multiple required categories unless the user explicitly asks for duplicate verification.
- If you revisit the same URL or entity and it does not add new required evidence, move to the next missing category or answer from the research notebook. Do not keep re-checking already satisfied categories.
- When enough evidence has been collected for every required category, answer from the research notebook instead of opening more pages merely to reconfirm.

Answer rules:
- Only answer with information visible in the current screenshot, candidate list, accessibility tree, or current page text excerpt.
- For commands, URLs, names, IDs, dates, and prices, copy the exact visible text.
- If the exact answer is not currently visible, keep navigating or scrolling instead of guessing.
- If the current website is irrelevant, blocked, broken, or lacks the required information, use search or navigate to move to a better public source.
- Prefer official, authoritative, public pages for factual answers.
- For filter-heavy pages, if the selected filters are encoded in the URL, query string, or hash, prefer navigate to a precise confirmed filtered URL over many repetitive checkbox/dropdown clicks.
- Preserve the user's language in answer and ask_user. If the task is Chinese, answer or ask follow-up questions in Chinese.
- Preserve Chinese characters exactly; never replace them with question marks.
- For multi-source research, travel planning, comparison, or report-style tasks, make the final answer evidence-rich and structured enough for an automatic HTML report: include concise sections, concrete findings, caveats, and source URLs.

Completion rules:
- If the current observation already satisfies the user's task, choose done or answer as appropriate.
- For tasks phrased like "stop/finish/done when X appears", choose done as soon as X is visible.

Checklist update rules:
- The current task checklist may be provided in human_hints with stable item ids.
- In every response, include "checklist_updates": [] if nothing changed.
- If the current page or action_history now satisfies, partially satisfies, or makes an item not applicable, include one update object using the exact checklist item id.
- Use status="done" only when enough distinct evidence has been gathered for that item. Use status="partial" when you found useful evidence but still need more fields, sources, or count.
- Do not mark an item partial or done merely because you started navigation, clicked a result, opened a search page, reached a homepage, plan to use a source, or saw a blocked/error/403 page. Update checklist only when the page or action_history contains useful evidence for the item.
- Put source URLs in evidence_urls when the current page or previous opened pages are evidence. Do not use a search-result page as final evidence if the underlying source page was not opened.
- In notes, briefly state the concrete entity/fact collected, such as hotel/restaurant/venue name plus visible fields. Do not repeat already completed items unless new evidence adds missing fields.
- Use the updated checklist to decide the next action: move to missing/partial items instead of re-checking completed entities.

Safety rules:
- Do not log in or use real credentials.
- Do not click payment, purchase, checkout, delete, remove, subscribe, submit, sign in, sign up, or other irreversible/high-risk controls.
- Do not type passwords, credit card numbers, phone numbers, addresses, or private information.
- Do not bypass CAPTCHA.
- Never invent a target_id. If an action needs a target, target_id must be one of the provided candidates.
- If a desired visual/accessibility-tree element is not present in Candidate Elements, do not click its ref. Choose another valid candidate, scroll, wait, or explain with answer/done if appropriate.
- If your previous action failed because target_id did not exist, choose a different valid candidate id from Candidate Elements.
- If required information is missing, unsafe, ambiguous, or needs a user choice, choose ask_user instead of guessing.
- For ask_user, put the exact concise question to the user in "text".
- Use target_id=null for scroll, press, back, wait, answer, done, ask_user, navigate, and search.
- For search boxes, use type with "key":"Enter" when you want to submit immediately; otherwise use a separate press action.
- For navigate, put the full URL in "text". For search, put only the search query in "text".

Required JSON schema:
{
  "thought": "brief reason for this single next action",
  "action": "click|type|scroll|press|back|wait|answer|done|ask_user|navigate|search",
  "target_id": 1,
  "text": "string or null",
  "direction": "up|down|null",
  "key": "string or null",
  "confidence": 0.0,
  "checklist_updates": [
    {
      "id": "exact checklist item id",
      "status": "done|partial|missing|not_applicable",
      "evidence_urls": ["https://..."],
      "notes": "brief evidence note"
    }
  ]
}
"""


def build_react_messages(
    task: str,
    current_url: str,
    candidates: list[CandidateElement],
    action_history: list[dict[str, Any]] | None = None,
    human_hints: list[str] | None = None,
    screenshot_path: Path | None = None,
    marked_screenshot_path: Path | None = None,
    accessibility_tree_text: str | None = None,
    page_text: str | None = None,
    include_screenshot_image: bool = False,
    max_accessibility_tree_chars: int = 28_000,
    max_page_text_chars: int = 8_000,
) -> list[ChatMessage]:
    observation = {
        "task": task,
        "current_url": current_url,
        "raw_screenshot_path": str(screenshot_path) if screenshot_path else None,
        "marked_screenshot_path": str(marked_screenshot_path) if marked_screenshot_path else None,
        "action_history": action_history or [],
        "human_hints": human_hints or [],
    }
    user_text = (
        f"Now given a task: {task}\n"
        "Please choose exactly one next browser action for the current page.\n"
        f"Current URL: {current_url}\n"
        f"{_screenshot_instruction(include_screenshot_image, screenshot_path, marked_screenshot_path)}\n\n"
        "# Observation Metadata\n"
        f"{json.dumps(observation, ensure_ascii=False, indent=2)}\n\n"
        f"{_human_hints_text(human_hints or [])}"
        "# Candidate Elements\n"
        "I've provided the tag name of each element and the text it contains if text exists. "
        "Refer to these element ids when choosing click/type targets.\n"
        f"{_candidates_text(candidates)}\n\n"
        f"{_recovery_notice(action_history or [], current_url)}"
        "# Current Page Text Excerpt\n"
        "This is compact rendered page text for reading and evidence gathering. "
        "Use Candidate Elements above for action target_ids.\n"
        f"{_compact_page_text(page_text or '', max_page_text_chars) or '(not available)'}\n\n"
        "# Current Page Accessibility Tree\n"
        f"{_truncate_text(accessibility_tree_text or '', max_accessibility_tree_chars) or '(not available)'}"
    )

    user_content: Any = user_text
    if include_screenshot_image and (screenshot_path or marked_screenshot_path):
        user_content = [
            {"type": "text", "text": user_text},
        ]
        if screenshot_path:
            user_content.extend(
                [
                    {"type": "text", "text": "Image 1: raw screenshot without Set-of-Marks annotations."},
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_to_data_url(screenshot_path)},
                    },
                ]
            )
        if marked_screenshot_path:
            user_content.extend(
                [
                    {"type": "text", "text": "Image 2: Set-of-Marks screenshot. Candidate numbers correspond to target_id."},
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_to_data_url(marked_screenshot_path)},
                    },
                ]
            )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def messages_to_prompt_text(messages: list[ChatMessage]) -> str:
    return "\n\n".join(f"{message['role'].upper()}:\n{_content_to_text(message['content'])}" for message in messages)


def _screenshot_instruction(
    include_screenshot_image: bool,
    screenshot_path: Path | None,
    marked_screenshot_path: Path | None,
) -> str:
    if include_screenshot_image and screenshot_path and marked_screenshot_path:
        return (
            "Observation: two screenshots are attached. Image 1 is the raw page screenshot for visual details. "
            "Image 2 is the Set-of-Marks screenshot for grounding actions to candidate target_ids. "
            "Use the raw screenshot to understand the page, and use the marked screenshot plus Candidate Elements to choose exact target_ids."
        )
    if include_screenshot_image and marked_screenshot_path:
        return (
            "Observation: please analyze the attached marked screenshot and give the Thought and Action. "
            "Focus more on the screenshot and then refer to the textual information."
        )
    if include_screenshot_image and screenshot_path:
        return (
            "Observation: please analyze the attached raw screenshot and textual element list. "
            "Use Candidate Elements for exact click/type target_ids."
        )
    return "Observation: please analyze the accessibility tree and textual element list. Screenshot pixels are not attached."


def _candidates_text(candidates: list[CandidateElement]) -> str:
    lines = []
    for candidate in candidates:
        text = _truncate_text(candidate.text, 120).replace("\n", " ")
        lines.append(
            "[{id}] <{tag}> role={role} text={text!r} bbox=({x:.1f},{y:.1f},{w:.1f},{h:.1f})".format(
                id=candidate.element_id,
                tag=candidate.tag,
                role=candidate.role or "",
                text=text,
                x=candidate.bbox.x,
                y=candidate.bbox.y,
                w=candidate.bbox.width,
                h=candidate.bbox.height,
            )
        )
    return "\n".join(lines) or "(none)"


def _human_hints_text(human_hints: list[str]) -> str:
    hints = [hint.strip() for hint in human_hints if hint and hint.strip()]
    if not hints:
        return ""
    lines = "\n".join(f"- {hint}" for hint in hints[-5:])
    return (
        "# Human Hints For This Continuation\n"
        "The user provided these suggestions while allowing the task to continue. "
        "Treat them as high-priority guidance, unless they conflict with safety rules or visible evidence.\n"
        f"{lines}\n\n"
    )


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}\n... [truncated {omitted} chars]"


def _compact_page_text(text: str, max_chars: int) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return _truncate_text("\n".join(lines), max_chars)


def _recovery_notice(action_history: list[dict[str, Any]], current_url: str) -> str:
    recent = action_history[-4:]
    if len(recent) < 2:
        return ""

    recent_reasons = " | ".join(str(item.get("progress") or "") for item in recent).lower()
    route_mismatch = _recent_navigation_mismatch(action_history[-8:], current_url)
    repeated_or_stuck = any(
        token in recent_reasons
        for token in (
            "repeated",
            "did not change",
            "same search landing",
            "navigation did not land",
            "loop",
            "stuck",
            "failed",
            "uncertain",
        )
    ) or route_mismatch
    if not repeated_or_stuck:
        return ""

    return (
        "# Recovery Notice\n"
        "Recent actions appear not to have advanced the useful task state. "
        "Think carefully before acting: identify any blocking overlay/dialog, verify whether an input was submitted, "
        "verify whether the current URL/state actually matches the requested filters, avoid repeating the same action or search detour, "
        "and choose one recovery action or ask_user if human help is needed.\n\n"
    )


def _recent_navigation_mismatch(action_history: list[dict[str, Any]], current_url: str) -> bool:
    normalized_current = _normalize_url(current_url)
    if not normalized_current:
        return False

    for item in action_history:
        action = item.get("action") or {}
        if str(action.get("action") or "").lower() != "navigate":
            continue
        requested = str(action.get("text") or "").strip()
        if not requested:
            continue
        parsed = urlparse(requested)
        if not parsed.query and not parsed.fragment:
            continue
        if _normalize_url(requested) != normalized_current:
            return True
    return False


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query = parsed.query.rstrip("&")
    fragment = parsed.fragment.rstrip("&")
    return f"{scheme}://{host}{path}?{query}#{fragment}"


def _image_to_data_url(path: Path) -> str:
    image_bytes = path.read_bytes()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif item.get("type") == "image_url":
                parts.append("[image_url attached; omitted from prompt.txt]")
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)
