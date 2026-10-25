from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from models.base_client import BaseChatClient, ChatMessage


CHECKLIST_BUILDER_PROMPT = """You are Web-VLMAgent's task checklist planner.

Convert the user's natural-language web task into a domain-neutral checklist for a browser research agent. The checklist should capture what evidence must be gathered and what the final answer must contain. Do not include step-by-step browsing instructions.

Return exactly one JSON object. Do not wrap it in markdown.

Schema:
{
  "items": [
    {
      "id": "short_id",
      "requirement": "clear requirement from the user task",
      "kind": "research|answer_format|safety|source",
      "status": "missing",
      "evidence_urls": [],
      "notes": "",
      "required_count": 3
    }
  ],
  "research_completion_ratio": 0.0,
  "overall_completion_ratio": 0.0,
  "enough_to_answer": false,
  "recommended_next_step": "continue",
  "missing_critical_items": ["..."],
  "suggested_agent_hint": "concise, domain-neutral hint"
}

Rules:
- Extract the user's actual requested facts, entities, comparisons, counts, source requirements, and output format.
- Keep it generic. Do not assume the domain is travel, hotels, academia, shopping, weather, or anything else unless the user task says so.
- Do not tell the agent exactly which website to use unless the user explicitly requires a website.
- Include source URL requirements when the user asks for evidence, citations, public pages, official pages, or real webpages.
- Include safety items only for prohibited operations that are implied by the task, such as no login, no purchase, no submission, no private data, no CAPTCHA bypass.
"""


CHECKLIST_AUDITOR_PROMPT = """You are Web-VLMAgent's task checklist auditor.

You receive a user's task, an initial checklist, visited URLs, and trace evidence. Decide whether the agent has gathered enough evidence to stop browsing and write a useful final answer, especially when it appears to be repeating redundant evidence collection.

Return exactly one JSON object. Do not wrap it in markdown.

Schema:
{
  "checklist": [
    {
      "id": "short_id",
      "requirement": "what must be satisfied",
      "kind": "research|answer_format|safety|source",
      "status": "done|partial|missing|not_applicable",
      "evidence_urls": ["https://..."],
      "notes": "short evidence-based note"
    }
  ],
  "research_completion_ratio": 0.0,
  "overall_completion_ratio": 0.0,
  "enough_to_answer": false,
  "recommended_next_step": "answer_now|continue|ask_user|fail",
  "missing_critical_items": ["..."],
  "suggested_agent_hint": "one concise instruction for the next step if continuing"
}

Rules:
- Use only the provided evidence. Do not invent facts or URLs.
- Research items are web facts that can be gathered before answering, such as hotel pages, restaurant pages, official pages, prices, ratings, opening hours, source URLs, product pages, papers, profiles, etc.
- Answer-format items are things that can be produced from existing evidence, such as itinerary formatting, tables, budget summary, caveats, rain plan, or final source list.
- If most research/source items are done and missing answer-format items can be written from the evidence, set enough_to_answer=true and recommended_next_step="answer_now".
- If evidence is incomplete but the next missing category is clear, set recommended_next_step="continue" and suggest a non-redundant next step.
- If the agent is revisiting already satisfied categories, mention that in suggested_agent_hint.
- Do not require perfect data when the website blocked prices, ratings, citations, or schedules; partial evidence plus an explicit caveat can be enough.
"""


FINAL_FROM_EVIDENCE_PROMPT = """You are Web-VLMAgent's final answer writer.

The browser agent has gathered enough evidence and is stopping because further browsing appears redundant. Write the final answer for the user from the provided trace evidence and checklist.

Rules:
- Use the user's language.
- Do not mention internal implementation details, checklist auditor, or failure status.
- Do not claim a fact unless it appears in the provided evidence or visited URLs.
- If some requested fields are unclear due to platform restrictions, include a clear caveat and continue with the best confirmed plan.
- Produce a complete, useful final answer that satisfies the user's task as much as possible.
- Include source URLs for the concrete hotel/restaurant/venue/product/profile/page facts you used.
"""


def build_initial_checklist(instruction: str) -> dict[str, Any]:
    items = [
        {
            "id": "main_goal",
            "requirement": "完成用户自然语言任务中的核心研究目标",
            "kind": "research",
            "status": "missing",
            "evidence_urls": [],
            "notes": "",
        },
        {
            "id": "required_facts",
            "requirement": "提取用户明确要求的全部可验证事实、指标、名称、时间、价格、评分、职位或其他字段",
            "kind": "research",
            "status": "missing",
            "evidence_urls": [],
            "notes": "",
        },
        {
            "id": "source_urls",
            "requirement": "保留关键事实对应的公开网页来源 URL",
            "kind": "source",
            "status": "missing",
            "evidence_urls": [],
            "notes": "",
        },
        {
            "id": "final_response",
            "requirement": "按用户要求的语言和格式生成结构清晰、可直接阅读的最终答案",
            "kind": "answer_format",
            "status": "missing",
            "evidence_urls": [],
            "notes": "",
        },
        {
            "id": "safety",
            "requirement": "不登录、不购买、不提交不可逆表单、不绕过验证码、不泄露私人信息",
            "kind": "safety",
            "status": "done",
            "evidence_urls": [],
            "notes": "Default safety constraint.",
        },
    ]

    return {
        "version": 1,
        "items": items,
        "research_completion_ratio": 0.0,
        "overall_completion_ratio": 0.0,
        "enough_to_answer": False,
        "recommended_next_step": "continue",
        "missing_critical_items": [item["requirement"] for item in items if item["kind"] in {"research", "source"}],
        "suggested_agent_hint": _initial_hint(items),
    }


def build_task_checklist(client: BaseChatClient, instruction: str) -> dict[str, Any]:
    fallback = build_initial_checklist(instruction)
    messages: list[ChatMessage] = [
        {"role": "system", "content": CHECKLIST_BUILDER_PROMPT},
        {"role": "user", "content": str(instruction or "").strip()},
    ]
    try:
        raw = client.complete(messages)
    except Exception:
        return fallback
    assessment = _parse_json_object(raw)
    return _normalize_assessment(assessment, fallback=fallback)


def assess_task_checklist(
    client: BaseChatClient,
    run_dir: Path,
    instruction: str,
    current_checklist: dict[str, Any],
    blocked_reason: str,
    max_steps: int = 30,
) -> dict[str, Any]:
    evidence = _collect_text_evidence(run_dir, max_steps=max_steps)
    visited_urls = _collect_visited_urls(run_dir)
    payload = {
        "user_task": instruction,
        "blocked_reason": blocked_reason,
        "current_checklist": current_checklist,
        "visited_urls": visited_urls,
        "trace_evidence": evidence,
    }
    messages: list[ChatMessage] = [
        {"role": "system", "content": CHECKLIST_AUDITOR_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]
    raw = client.complete(messages)
    assessment = _parse_json_object(raw)
    return _normalize_assessment(assessment, fallback=current_checklist)


def generate_final_answer_from_evidence(
    client: BaseChatClient,
    run_dir: Path,
    instruction: str,
    checklist_assessment: dict[str, Any],
    max_steps: int = 34,
) -> str:
    payload = {
        "user_task": instruction,
        "checklist_assessment": checklist_assessment,
        "visited_urls": _collect_visited_urls(run_dir),
        "trace_evidence": _collect_text_evidence(run_dir, max_steps=max_steps),
    }
    messages: list[ChatMessage] = [
        {"role": "system", "content": FINAL_FROM_EVIDENCE_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]
    return client.complete(messages).strip()


def checklist_hint(checklist: dict[str, Any]) -> str:
    items = checklist.get("items") or checklist.get("checklist") or []
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        label = str(item.get("requirement") or item.get("id") or "").strip()
        status = str(item.get("status") or "missing").lower()
        kind = str(item.get("kind") or "research").lower()
        notes = str(item.get("notes") or "").strip()
        urls = item.get("evidence_urls") if isinstance(item.get("evidence_urls"), list) else []
        required_count = item.get("required_count")
        if not item_id or not label:
            continue
        count_text = f" required_count={required_count}" if required_count is not None else ""
        evidence_text = f" evidence_urls={urls[:4]}" if urls else ""
        notes_text = f" notes={notes}" if notes else ""
        lines.append(f"- [{item_id}] status={status} kind={kind}{count_text}: {label}{evidence_text}{notes_text}")
    suggested = str(checklist.get("suggested_agent_hint") or "").strip()
    parts = [
        "Current task checklist. Use exact ids in checklist_updates. "
        "Update an item as soon as the current page or action_history provides evidence. "
        "Do not repeat items already marked done unless adding genuinely missing fields."
    ]
    if lines:
        parts.append("\n".join(lines[:12]))
    if suggested and not suggested.startswith("Current task checklist."):
        parts.append("Recommended next move: " + suggested)
    return " ".join(parts)


def should_answer_from_assessment(assessment: dict[str, Any]) -> bool:
    if str(assessment.get("recommended_next_step") or "").lower() == "answer_now":
        return True
    if bool(assessment.get("enough_to_answer")):
        return True
    try:
        research_ratio = float(assessment.get("research_completion_ratio") or 0.0)
        overall_ratio = float(assessment.get("overall_completion_ratio") or 0.0)
    except (TypeError, ValueError):
        return False
    return research_ratio >= 0.72 and overall_ratio >= 0.55


def apply_checklist_updates(
    checklist: dict[str, Any],
    updates: list[dict[str, Any]] | None,
    current_url: str = "",
) -> dict[str, Any]:
    if not updates:
        return _normalize_assessment(checklist, fallback=checklist)

    merged = _normalize_assessment(dict(checklist), fallback=checklist)
    items = merged.get("items") if isinstance(merged.get("items"), list) else []
    by_id = {str(item.get("id")): item for item in items if isinstance(item, dict)}
    status_rank = {"missing": 0, "partial": 1, "done": 2, "not_applicable": 2}

    for update in updates:
        if not isinstance(update, dict):
            continue
        item_id = str(update.get("id") or "").strip()
        if not item_id or item_id not in by_id:
            continue
        item = by_id[item_id]
        new_status = str(update.get("status") or "").lower()
        if new_status not in {"done", "partial", "missing", "not_applicable"}:
            continue
        update_urls = update.get("evidence_urls") if isinstance(update.get("evidence_urls"), list) else []
        note = str(update.get("notes") or "").strip()
        if _is_non_evidence_update(item, new_status, update_urls, note):
            continue
        old_status = str(item.get("status") or "missing").lower()
        if status_rank.get(new_status, 0) < status_rank.get(old_status, 0):
            new_status = old_status
        item["status"] = new_status

        existing_urls = [str(url) for url in item.get("evidence_urls", []) if str(url).strip()]
        for url in [*update_urls, current_url if new_status in {"done", "partial"} else ""]:
            clean_url = str(url or "").strip()
            if clean_url.startswith(("http://", "https://", "file://")) and clean_url not in existing_urls:
                existing_urls.append(clean_url)
        item["evidence_urls"] = existing_urls[:12]

        if note:
            old_note = str(item.get("notes") or "").strip()
            if old_note and note not in old_note:
                item["notes"] = f"{old_note} | {note}"
            else:
                item["notes"] = note or old_note

    merged["items"] = items
    merged["checklist"] = items
    merged["research_completion_ratio"] = _ratio(items, kind_filter={"research", "source"}, explicit=None)
    merged["overall_completion_ratio"] = _ratio(items, kind_filter=None, explicit=None)
    missing = [
        item["id"]
        for item in items
        if item.get("status") in {"missing", "partial"} and item.get("kind") in {"research", "source"}
    ]
    merged["missing_critical_items"] = missing[:10]
    research_done = not missing
    merged["enough_to_answer"] = bool(merged.get("enough_to_answer")) or research_done
    if research_done:
        merged["recommended_next_step"] = "answer_now"
        merged["suggested_agent_hint"] = "Research/source checklist items are covered. Write the final answer from the collected evidence instead of opening more pages."
    else:
        merged["recommended_next_step"] = "continue"
        merged["suggested_agent_hint"] = _next_missing_hint(items)
    return merged


def _initial_hint(items: list[dict[str, Any]]) -> str:
    research = [item["requirement"] for item in items if item.get("kind") in {"research", "source"}]
    if not research:
        return "Proceed with the task, keep source evidence, and answer when enough evidence is collected."
    return "Collect distinct source evidence for: " + "; ".join(research[:6])


def _next_missing_hint(items: list[dict[str, Any]]) -> str:
    missing = [
        f"{item.get('id')}: {item.get('requirement')}"
        for item in items
        if item.get("status") in {"missing", "partial"} and item.get("kind") in {"research", "source"}
    ]
    if not missing:
        return "Answer from the collected evidence; do not open more pages just to reconfirm."
    return "Continue with missing or partial checklist items only: " + "; ".join(str(item) for item in missing[:4])


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_assessment(assessment: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    if not assessment:
        assessment = dict(fallback)
    items = assessment.get("checklist") or assessment.get("items") or fallback.get("items") or []
    if not isinstance(items, list):
        items = []
    normalized_items: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "missing").lower()
        if status not in {"done", "partial", "missing", "not_applicable"}:
            status = "missing"
        kind = str(item.get("kind") or "research").lower()
        if kind not in {"research", "answer_format", "safety", "source"}:
            kind = "research"
        if kind == "safety" and status == "missing":
            status = "done"
            if not str(item.get("notes") or "").strip():
                item = {**item, "notes": "Default safety constraint; no unsafe action has been requested or performed."}
        urls = item.get("evidence_urls") if isinstance(item.get("evidence_urls"), list) else []
        normalized: dict[str, Any] = {
            "id": str(item.get("id") or f"item_{index + 1}"),
            "requirement": str(item.get("requirement") or item.get("id") or ""),
            "kind": kind,
            "status": status,
            "evidence_urls": [str(url) for url in urls[:8]],
            "notes": str(item.get("notes") or ""),
        }
        if item.get("required_count") is not None:
            normalized["required_count"] = item.get("required_count")
        normalized_items.append(normalized)
    assessment["items"] = normalized_items
    assessment["checklist"] = normalized_items
    assessment["research_completion_ratio"] = _ratio(
        normalized_items,
        kind_filter={"research", "source"},
        explicit=assessment.get("research_completion_ratio"),
    )
    assessment["overall_completion_ratio"] = _ratio(
        normalized_items,
        kind_filter=None,
        explicit=assessment.get("overall_completion_ratio"),
    )
    assessment["enough_to_answer"] = bool(assessment.get("enough_to_answer"))
    next_step = str(assessment.get("recommended_next_step") or "continue").lower()
    if next_step not in {"answer_now", "continue", "ask_user", "fail"}:
        next_step = "continue"
    assessment["recommended_next_step"] = next_step
    missing = assessment.get("missing_critical_items")
    if not isinstance(missing, list):
        missing = [
            item["requirement"]
            for item in normalized_items
            if item["status"] in {"missing", "partial"} and item["kind"] in {"research", "source"}
        ]
    assessment["missing_critical_items"] = [str(item) for item in missing[:10]]
    assessment["suggested_agent_hint"] = str(assessment.get("suggested_agent_hint") or checklist_hint(assessment))
    return assessment


def _ratio(items: list[dict[str, Any]], kind_filter: set[str] | None, explicit: Any) -> float:
    try:
        value = float(explicit)
        if 0.0 <= value <= 1.0:
            return value
    except (TypeError, ValueError):
        pass
    filtered = [item for item in items if kind_filter is None or item.get("kind") in kind_filter]
    applicable = [item for item in filtered if item.get("status") != "not_applicable"]
    if not applicable:
        return 0.0
    score = 0.0
    for item in applicable:
        if item.get("status") == "done":
            score += 1.0
        elif item.get("status") == "partial":
            score += 0.5
    return round(score / len(applicable), 3)


def _is_non_evidence_update(
    item: dict[str, Any],
    status: str,
    evidence_urls: list[Any],
    note: str,
) -> bool:
    if status not in {"done", "partial"}:
        return False
    if str(item.get("kind") or "").lower() not in {"research", "source"}:
        return False
    clean_urls = [str(url).strip() for url in evidence_urls if str(url).strip()]
    if clean_urls:
        return False
    normalized = note.strip().lower()
    if not normalized:
        return True
    weak_phrases = (
        "initiated navigation",
        "start navigation",
        "started navigation",
        "navigate to",
        "navigating to",
        "trying ",
        "try ",
        "searching for",
        "searched for",
        "clicked result",
        "opened search",
        "plan to",
        "will use",
        "attempt",
        "403",
        "forbidden",
        "blocked",
        "error page",
        "开始导航",
        "尝试",
        "搜索",
        "计划",
        "准备",
        "403",
        "禁止访问",
        "被拦截",
    )
    return any(phrase in normalized for phrase in weak_phrases)


def _collect_visited_urls(run_dir: Path) -> list[str]:
    urls: list[str] = []
    for step_dir in sorted(path for path in run_dir.glob("step_*") if path.is_dir()):
        execution = _load_json(step_dir / "execution_result.json")
        for key in ("url_before", "url_after"):
            value = str(execution.get(key) or "").strip()
            if value.startswith(("http://", "https://", "file://")) and value not in urls:
                urls.append(value)
    return urls[:60]


def _collect_text_evidence(run_dir: Path, max_steps: int) -> list[dict[str, Any]]:
    step_dirs = sorted(path for path in run_dir.glob("step_*") if path.is_dir())
    selected = _spread_select(step_dirs, max_steps)
    evidence: list[dict[str, Any]] = []
    for step_dir in selected:
        parsed_action = _load_json(step_dir / "parsed_action.json")
        execution = _load_json(step_dir / "execution_result.json")
        progress = _load_json(step_dir / "progress_check.json")
        page_text = ""
        page_text_path = step_dir / "page_text.txt"
        if page_text_path.exists():
            page_text = _truncate_text(page_text_path.read_text(encoding="utf-8", errors="replace"), 1500)
        evidence.append(
            {
                "step": step_dir.name,
                "action": parsed_action.get("action"),
                "action_text": parsed_action.get("text") or "",
                "url_after": execution.get("url_after") or execution.get("url_before") or "",
                "progress": progress.get("reason") or "",
                "page_text_excerpt": page_text,
            }
        )
    return evidence


def _spread_select(items: list[Path], limit: int) -> list[Path]:
    if limit <= 0 or len(items) <= limit:
        return items
    if limit == 1:
        return [items[-1]]
    selected: list[Path] = []
    last_index = len(items) - 1
    for slot in range(limit):
        index = round(slot * last_index / (limit - 1))
        item = items[index]
        if item not in selected:
            selected.append(item)
    return selected


def _truncate_text(text: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]} ... [truncated {len(compact) - max_chars} chars]"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
