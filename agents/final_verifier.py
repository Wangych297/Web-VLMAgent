from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


STOPWORDS = {
    "a",
    "an",
    "and",
    "box",
    "button",
    "click",
    "find",
    "for",
    "in",
    "into",
    "of",
    "on",
    "or",
    "page",
    "search",
    "the",
    "to",
    "type",
}
OPERATION_WORDS = {"type", "search", "click", "press", "scroll", "open", "find"}


@dataclass
class FinalVerifier:
    min_keyword_matches: int = 1

    def verify(
        self,
        instruction: str,
        final_action: dict[str, Any],
        current_url: str,
        page_text: str,
        action_history: list[dict[str, Any]],
        execution_result: dict[str, Any],
    ) -> dict[str, Any]:
        action_name = str(final_action.get("action") or "")
        answer_text = str(final_action.get("text") or "")
        keywords = _keywords(instruction)
        evidence_text = " ".join([page_text, current_url]).lower()
        matched_keywords = [keyword for keyword in keywords if keyword.lower() in evidence_text]

        if action_name == "answer":
            return self._verify_answer(
                answer_text=answer_text,
                page_text=page_text,
                current_url=current_url,
                matched_keywords=matched_keywords,
            )

        if action_name == "done":
            return self._verify_done(
                instruction=instruction,
                final_action=final_action,
                page_text=page_text,
                action_history=action_history,
                execution_result=execution_result,
                matched_keywords=matched_keywords,
            )

        return {
            "success": False,
            "reason": "not a final action",
            "evidence": {
                "final_action": action_name,
                "answer_text": answer_text,
                "matched_keywords": matched_keywords,
            },
        }

    def _verify_answer(
        self,
        answer_text: str,
        page_text: str,
        current_url: str,
        matched_keywords: list[str],
    ) -> dict[str, Any]:
        answer_supported = _answer_supported_by_evidence(answer_text, page_text, current_url)
        keyword_match_ok = len(matched_keywords) >= self.min_keyword_matches
        # Also check if answer contains numeric/ASCII substrings that match evidence
        # (robust against encoding issues with CJK characters)
        ascii_supported = _ascii_parts_supported(answer_text, page_text, current_url)

        if not answer_text.strip():
            success = False
            reason = "answer action has empty text"
        elif answer_supported and keyword_match_ok:
            success = True
            reason = "answer text is present in current page evidence"
        elif ascii_supported and keyword_match_ok:
            success = True
            reason = "answer numeric/ASCII parts found in page evidence (CJK encoding fallback)"
        elif not answer_supported and not ascii_supported:
            success = False
            reason = "uncertain: answer text is not present in current page evidence"
        else:
            success = False
            reason = "uncertain: answer text does not match enough task keywords"

        return {
            "success": success,
            "reason": reason,
            "evidence": {
                "final_action": "answer",
                "answer_text": answer_text,
                "matched_keywords": matched_keywords,
                "answer_supported_by_evidence": answer_supported or ascii_supported,
            },
        }

    def _verify_done(
        self,
        instruction: str,
        final_action: dict[str, Any],
        page_text: str,
        action_history: list[dict[str, Any]],
        execution_result: dict[str, Any],
        matched_keywords: list[str],
    ) -> dict[str, Any]:
        operation_task = bool(OPERATION_WORDS.intersection(_tokens(instruction)))
        recent_successful_action = _last_successful_non_final_action(action_history)
        typed_texts = [
            str(item.get("action", {}).get("text") or "")
            for item in action_history
            if item.get("action", {}).get("action") == "type" and item.get("execution_result", {}).get("ok")
        ]
        typed_text_matched = any(text and text.lower() in page_text.lower() for text in typed_texts)
        literal_evidence = _normalize_literal_text("\n".join([page_text, *typed_texts]))
        required_literals = _required_literals(instruction)
        missing_literals = [
            literal for literal in required_literals if _normalize_literal_text(literal) not in literal_evidence
        ]

        if missing_literals:
            success = False
            reason = "uncertain: missing required literal evidence"
        elif operation_task and recent_successful_action and (typed_text_matched or matched_keywords):
            success = True
            reason = "operation task has a recent successful action with matching page evidence"
        elif operation_task and recent_successful_action:
            success = True
            reason = "operation task has a recent successful action, but evidence is weak"
        elif not operation_task and matched_keywords and execution_result.get("ok"):
            success = True
            reason = "done action has matching task keywords in current page evidence"
        else:
            success = False
            reason = "uncertain: rule-based verifier cannot confirm task completion"

        return {
            "success": success,
            "reason": reason,
            "evidence": {
                "final_action": final_action.get("action"),
                "answer_text": str(final_action.get("text") or ""),
                "matched_keywords": matched_keywords,
                "recent_successful_action": recent_successful_action,
                "typed_texts": typed_texts,
                "required_literals": required_literals,
                "missing_literals": missing_literals,
            },
        }


def default_final_verification(final_action: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "reason": "not a final action",
        "evidence": {
            "final_action": (final_action or {}).get("action"),
            "answer_text": (final_action or {}).get("text") or "",
            "matched_keywords": [],
        },
    }


def _last_successful_non_final_action(action_history: list[dict[str, Any]]) -> str | None:
    for item in reversed(action_history):
        action_name = item.get("action", {}).get("action")
        if action_name in {"answer", "done"}:
            continue
        if item.get("execution_result", {}).get("ok"):
            return str(action_name)
    return None


def _keywords(text: str) -> list[str]:
    keywords = [token for token in _tokens(text) if token not in STOPWORDS and len(token) >= 3]
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if len(sequence) <= 6:
            keywords.append(sequence)
            continue
        keywords.extend(sequence[index : index + 2] for index in range(0, len(sequence) - 1))
    return keywords


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9_-]+", text.lower()))


def _required_literals(text: str) -> list[str]:
    literals = set(re.findall(r"[\w.+-]+@[\w.-]+\.\w+", text.lower()))
    literals.update(token for token in re.findall(r"\b\d{2,}\b", text) if token)
    return sorted(literals)


def _normalize_literal_text(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9@.]+", "", text.lower())


def _answer_supported_by_evidence(answer_text: str, page_text: str, current_url: str) -> bool:
    normalized_answer = _normalize_for_match(answer_text)
    if not normalized_answer:
        return False
    normalized_evidence = _normalize_for_match(f"{page_text}\n{current_url}")
    if normalized_answer in normalized_evidence:
        return True
    for span in _answer_evidence_spans(answer_text):
        normalized_span = _normalize_for_match(span)
        if normalized_span and normalized_span in normalized_evidence:
            return True
    return False


def _normalize_for_match(text: str) -> str:
    normalized = text.lower()
    normalized = normalized.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _answer_evidence_spans(answer_text: str) -> list[str]:
    spans: list[str] = []
    spans.extend(span.rstrip(".,:;!?") for span in re.findall(r"https?://[^\s,;)\]}]+", answer_text))
    spans.extend(re.findall(r'"([^"]{2,})"', answer_text))
    spans.extend(re.findall(r"'([^']{2,})'", answer_text))
    spans.extend(re.findall(r"[“「『]([^”」』]{1,30})[”」』]", answer_text))
    spans.extend(re.findall(r"\d+\s*[~～-]\s*\d+\s*°?\s*[Cc]?", answer_text))
    spans.extend(re.findall(r"\d+\s*°\s*[Cc]?", answer_text))
    spans.extend(re.findall(r"[东南西北东北东南西南西北]+风\s*[一二三四五六七八九十\d]+级", answer_text))
    spans.extend(re.findall(r"[\u4e00-\u9fff]{1,6}\s*[一二三四五六七八九十\d]+级", answer_text))
    command_pattern = re.compile(
        r"\b(?:pip|uv|npm|yarn|pnpm|python|python3|conda|git|curl|docker)\s+[^\n.;]+",
        re.IGNORECASE,
    )
    spans.extend(match.group(0).strip() for match in command_pattern.finditer(answer_text))
    return spans


def _ascii_parts_supported(answer_text: str, page_text: str, current_url: str) -> bool:
    """Fallback: extract ASCII/numeric substrings from answer and check against evidence.

    This handles CJK encoding corruption where Chinese characters become garbled
    but numbers and English words remain intact.
    """
    import re as _re

    evidence = _normalize_for_match(f"{page_text}\n{current_url}")
    # Extract numeric patterns (integers, decimals) from answer
    numbers = _re.findall(r"\b\d+(?:\.\d+)?\b", answer_text)
    # Extract ASCII word sequences (3+ chars) from answer
    words = _re.findall(r"[a-zA-Z]{3,}", answer_text)

    candidates = numbers + words
    if not candidates:
        return False

    # Require at least 2 non-trivial matches
    matches = 0
    for candidate in candidates:
        normalized = candidate.lower()
        if len(normalized) >= 3 and normalized in evidence:
            matches += 1

    return matches >= 2
