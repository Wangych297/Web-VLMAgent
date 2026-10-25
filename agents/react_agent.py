from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from browser.actions import ActionName
from models.base_client import BaseChatClient, ChatMessage


class ChecklistUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["done", "partial", "missing", "not_applicable"]
    evidence_urls: list[str] = Field(default_factory=list)
    notes: str = ""


class ModelAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thought: str
    action: ActionName
    target_id: int | None = None
    text: str | None = None
    direction: Literal["up", "down"] | None = None
    key: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    checklist_updates: list[ChecklistUpdate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "ModelAction":
        if self.action == "type" and not (self.text or "").strip():
            raise ValueError("type requires non-empty text")
        if self.action == "scroll" and self.direction not in {"up", "down"}:
            raise ValueError("scroll requires direction 'up' or 'down'")
        if self.action == "press" and not (self.key or "").strip():
            raise ValueError("press requires non-empty key")
        if self.action == "ask_user" and not (self.text or "").strip():
            raise ValueError("ask_user requires a clarification question in text")
        if self.action == "navigate":
            parsed = urlparse((self.text or "").strip())
            if parsed.scheme not in {"http", "https", "file"}:
                raise ValueError("navigate requires a full http://, https://, or file:// URL in text")
        if self.action == "search" and not (self.text or "").strip():
            raise ValueError("search requires a non-empty query in text")
        return self

    def to_browser_action(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target_id": self.target_id,
            "text": self.text or "",
            "direction": self.direction or "down",
            "key": self.key or "",
        }


@dataclass(frozen=True)
class ReactAgentResult:
    parsed_action: ModelAction
    raw_responses: list[str]
    parse_error: str | None = None
    fallback_used: bool = False


class SingleStepReactAgent:
    def __init__(self, client: BaseChatClient) -> None:
        self.client = client

    def decide(self, messages: list[ChatMessage]) -> ReactAgentResult:
        raw_responses: list[str] = []
        last_error: str | None = None
        current_messages = list(messages)

        max_parse_attempts = 3
        for attempt in range(max_parse_attempts):
            raw_response = self.client.complete(current_messages)
            raw_responses.append(raw_response)

            try:
                return ReactAgentResult(
                    parsed_action=parse_model_action(raw_response),
                    raw_responses=raw_responses,
                    parse_error=None,
                    fallback_used=False,
                )
            except ValueError as exc:
                last_error = str(exc)
                if attempt < max_parse_attempts - 1:
                    current_messages = [
                        *messages,
                        {"role": "assistant", "content": raw_response},
                        {
                            "role": "user",
                            "content": (
                                "Your previous response was invalid. "
                                f"Error: {last_error}\n"
                                "Return exactly one valid JSON object matching the required schema. "
                                "No markdown, no explanation outside JSON."
                            ),
                        },
                    ]

        return ReactAgentResult(
            parsed_action=safe_wait_action(last_error or "invalid model output"),
            raw_responses=raw_responses,
            parse_error=last_error,
            fallback_used=True,
        )


def parse_model_action(raw_response: str) -> ModelAction:
    try:
        data = json.loads(_extract_json_text(raw_response))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    try:
        return ModelAction.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"invalid action schema: {exc}") from exc


def safe_wait_action(reason: str) -> ModelAction:
    return ModelAction(
        thought=f"Fallback wait because model output was invalid: {reason}",
        action="wait",
        target_id=None,
        text=None,
        direction=None,
        key=None,
        confidence=0.0,
    )


def _extract_json_text(raw_response: str) -> str:
    text = raw_response.strip()

    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]
