from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from models.base_client import BaseChatClient, ModelClientError


PLANNER_SYSTEM_PROMPT = """You are the task planner for Web-VLMAgent, a web-specialist agent.

Return exactly one JSON object. Do not wrap it in markdown.

Your job is not to solve the task yet. Your job is to decide how the browser
agent should start.

Rules:
- Prefer real, public, authoritative websites and official source pages.
- Avoid sites that are likely to block automation, show CAPTCHA, require login, require payment, or need private data.
- If the user's request is ambiguous or missing a necessary choice, return ask_user.
- If the user provides a start URL, use it unless it is unsafe or irrelevant.
- If no single authoritative site is obvious, choose a reasonable search-engine results page as the start URL.
- Do not hard-code one website for a whole category; choose the best start URL for the specific task.
- Never plan login, payment, purchase, deletion, irreversible submission, CAPTCHA bypass, or private-data entry.
- Preserve the user's language. If the task is written in Chinese, write refined_instruction, question, reason, and risk_notes in Chinese.
- Preserve Chinese characters exactly; never replace Chinese text with question marks or transliteration.

Schema:
{
  "status": "run|ask_user",
  "start_url": "https://...",
  "refined_instruction": "browser-agent instruction preserving the user's goal",
  "question": "concise question if status=ask_user, otherwise empty",
  "reason": "brief planning rationale",
  "risk_notes": "safety/environment notes"
}
"""


@dataclass(frozen=True)
class TaskPlan:
    status: Literal["run", "ask_user"]
    start_url: str
    refined_instruction: str
    question: str
    reason: str
    risk_notes: str
    raw_response: str


class TaskPlanner:
    def __init__(self, client: BaseChatClient) -> None:
        self.client = client

    def plan(self, instruction: str, user_start_url: str = "") -> TaskPlan:
        prompt = (
            f"User task:\n{instruction.strip()}\n\n"
            f"User-provided start URL, if any:\n{user_start_url.strip() or '(none)'}\n\n"
            "Create the browser start plan now."
        )
        raw_response = self.client.complete(
            [
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )
        return parse_task_plan(raw_response, instruction)


def parse_task_plan(raw_response: str, fallback_instruction: str) -> TaskPlan:
    data = _parse_json_object(raw_response)
    status = str(data.get("status") or "").strip().lower()
    start_url = str(data.get("start_url") or "").strip()
    refined_instruction = str(data.get("refined_instruction") or fallback_instruction).strip()
    question = str(data.get("question") or "").strip()
    reason = str(data.get("reason") or "").strip()
    risk_notes = str(data.get("risk_notes") or "").strip()

    if status not in {"run", "ask_user"}:
        raise ModelClientError(f"planner returned unsupported status: {status!r}")
    if status == "ask_user":
        if not question:
            raise ModelClientError("planner returned ask_user without a question")
        return TaskPlan(
            status="ask_user",
            start_url="",
            refined_instruction=refined_instruction or fallback_instruction,
            question=question,
            reason=reason,
            risk_notes=risk_notes,
            raw_response=raw_response,
        )

    parsed = urlparse(start_url)
    if parsed.scheme not in {"http", "https", "file"}:
        raise ModelClientError(f"planner returned invalid start_url: {start_url!r}")
    return TaskPlan(
        status="run",
        start_url=start_url,
        refined_instruction=refined_instruction or fallback_instruction,
        question="",
        reason=reason,
        risk_notes=risk_notes,
        raw_response=raw_response,
    )


def _parse_json_object(raw_response: str) -> dict[str, object]:
    text = raw_response.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    if not (text.startswith("{") and text.endswith("}")):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ModelClientError("planner response is not a JSON object")
    return payload
