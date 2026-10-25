from __future__ import annotations

from typing import Any, Iterable, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


ActionName = Literal[
    "click",
    "type",
    "scroll",
    "press",
    "back",
    "wait",
    "answer",
    "done",
    "ask_user",
    "navigate",
    "search",
]
ScrollDirection = Literal["up", "down"]


class ActionValidationError(ValueError):
    """Raised when a structured browser action is not executable."""


class BBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    width: float
    height: float


class CandidateElement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: int
    tag: str
    role: str = ""
    text: str = ""
    selector: str
    bbox: BBox
    visible: bool
    enabled: bool


class BrowserAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ActionName
    target_id: int | None = None
    text: str = ""
    direction: ScrollDirection = "down"
    key: str = "Enter"

    @field_validator("text", "key", mode="before")
    @classmethod
    def none_to_empty_string(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value)


TARGET_ACTIONS = {"click", "type"}
DANGEROUS_CLICK_TERMS = (
    "delete",
    "remove",
    "purchase",
    "buy",
    "pay",
    "payment",
    "checkout",
    "order",
    "subscribe",
    "login",
    "log in",
    "sign in",
    "sign up",
    "submit",
)
SENSITIVE_TYPE_TERMS = (
    "password",
    "passcode",
    "credit card",
    "card number",
    "cvv",
    "address",
    "ssn",
    "sensitive",
)
CONTACT_TYPE_TERMS = (
    "email",
    "phone",
    "telephone",
    "mobile",
)


def parse_candidates(candidates: Iterable[CandidateElement | dict[str, Any]]) -> list[CandidateElement]:
    return [
        candidate if isinstance(candidate, CandidateElement) else CandidateElement.model_validate(candidate)
        for candidate in candidates
    ]


def candidate_map(candidates: Iterable[CandidateElement | dict[str, Any]]) -> dict[int, CandidateElement]:
    parsed = parse_candidates(candidates)
    return {candidate.element_id: candidate for candidate in parsed}


def get_candidate_by_id(
    candidates: Iterable[CandidateElement | dict[str, Any]],
    target_id: int,
) -> CandidateElement:
    candidates_by_id = candidate_map(candidates)
    if target_id not in candidates_by_id:
        raise ActionValidationError(f"target_id {target_id} does not exist in candidates")
    return candidates_by_id[target_id]


def validate_action(
    action_data: BrowserAction | dict[str, Any],
    candidates: Iterable[CandidateElement | dict[str, Any]],
) -> BrowserAction:
    try:
        action = action_data if isinstance(action_data, BrowserAction) else BrowserAction.model_validate(action_data)
    except ValidationError as exc:
        raise ActionValidationError(str(exc)) from exc

    if action.action not in TARGET_ACTIONS:
        action = action.model_copy(update={"target_id": None})

    candidates_by_id = candidate_map(candidates)

    if action.action in TARGET_ACTIONS:
        if action.target_id is None:
            raise ActionValidationError(f"{action.action} requires target_id")
        if action.target_id not in candidates_by_id:
            raise ActionValidationError(f"target_id {action.target_id} does not exist in candidates")
        target = candidates_by_id[action.target_id]
        if not target.visible:
            raise ActionValidationError(f"target_id {action.target_id} is not visible")
        if not target.enabled:
            raise ActionValidationError(f"target_id {action.target_id} is not enabled")
        if action.action == "click" and _contains_any(_target_text(target), DANGEROUS_CLICK_TERMS):
            raise ActionValidationError(f"target_id {action.target_id} appears to be a high-risk click target")
        target_text = _target_text(target)
        if action.action == "type" and _contains_any(target_text, SENSITIVE_TYPE_TERMS):
            raise ActionValidationError(f"target_id {action.target_id} appears to request sensitive input")
        if (
            action.action == "type"
            and _contains_any(target_text, CONTACT_TYPE_TERMS)
            and not _looks_like_synthetic_contact(action.text)
        ):
            raise ActionValidationError(
                f"target_id {action.target_id} appears to request contact information; use synthetic test data only"
            )
        if action.action == "type" and not action.text.strip():
            raise ActionValidationError("type requires non-empty text")

    if action.action == "press" and not action.key.strip():
        raise ActionValidationError("press requires a non-empty key")
    if action.action == "navigate":
        parsed = urlparse(action.text.strip())
        if parsed.scheme not in {"http", "https", "file"}:
            raise ActionValidationError("navigate requires a full http://, https://, or file:// URL in text")
    if action.action == "search" and not action.text.strip():
        raise ActionValidationError("search requires a non-empty query in text")

    return action


def _target_text(candidate: CandidateElement) -> str:
    return " ".join(
        [
            candidate.tag,
            candidate.role,
            candidate.text,
            candidate.selector,
        ]
    ).lower()


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def _looks_like_synthetic_contact(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    synthetic_tokens = (
        "example.com",
        "example.org",
        "example.net",
        "test",
        "demo",
        "fake",
        "555",
        "000",
    )
    return any(token in normalized for token in synthetic_tokens)
