from __future__ import annotations

import json
from json import JSONDecodeError
from collections import Counter
from pathlib import Path
from typing import Any


FAILURE_TYPES = ("perception", "planning", "execution", "verification", "website")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (JSONDecodeError, OSError):
        return default


def collect_run_records(run_dirs: list[Path]) -> list[dict[str, Any]]:
    return [build_run_record(run_dir) for run_dir in run_dirs]


def build_run_record(run_dir: Path) -> dict[str, Any]:
    summary = load_json(run_dir / "run_summary.json", {})
    harness_success = _summary_success(summary)
    steps_used = int(summary.get("steps_used") or 0)
    if steps_used > 0:
        step_dirs = [run_dir / f"step_{index:03d}" for index in range(steps_used)]
    else:
        step_dirs = sorted(path for path in run_dir.glob("step_*") if path.is_dir())
    steps = [_load_step_record(step_dir) for step_dir in step_dirs]
    task_metadata = summary.get("task_metadata", {})
    validator_result = load_json(run_dir / "validator_result.json", {})
    has_validator_result = isinstance(validator_result, dict) and bool(validator_result)
    validator_success = validator_result.get("success") if has_validator_result else None
    has_binary_validator = isinstance(validator_success, bool)
    if has_binary_validator:
        validated_success: bool | str = validator_success
    elif has_validator_result:
        validated_success = ""
    else:
        validated_success = ""

    total_steps = len(steps)
    valid_json_steps = sum(1 for step in steps if _action_json_valid(step))
    grounding_errors = sum(1 for step in steps if _is_grounding_error(step))
    retry_used = any(step.get("execution_result", {}).get("model_retry_used") for step in steps)
    failure_type = "" if harness_success else classify_failure(summary, steps)

    return {
        "task_id": task_metadata.get("task_id", ""),
        "site": task_metadata.get("site", ""),
        "difficulty": task_metadata.get("difficulty", ""),
        "run_id": summary.get("run_id", run_dir.name),
        "provider": summary.get("provider", task_metadata.get("provider", "")),
        "model": summary.get("model", task_metadata.get("model", "")),
        "observation_mode": task_metadata.get(
            "observation_mode",
            "vision" if summary.get("include_screenshot_images") else "text",
        ),
        "success": harness_success,
        "harness_success": harness_success,
        "validator_success": validator_success if has_binary_validator else "",
        "validator_needs_review": bool(validator_result.get("needs_review")) if has_validator_result else "",
        "validated_success": validated_success,
        "validator_score": validator_result.get("score", "") if isinstance(validator_result, dict) else "",
        "validator_evaluator": validator_result.get("evaluator", "") if isinstance(validator_result, dict) else "",
        "validator_reason": validator_result.get("reason", "") if isinstance(validator_result, dict) else "",
        "final_status": summary.get("final_status", ""),
        "steps_used": int(summary.get("steps_used") or total_steps),
        "duration_seconds": float(summary.get("duration_seconds") or 0.0),
        "trace_dir": str(run_dir),
        "final_reason": _summary_final_reason(summary),
        "action_json_valid_steps": valid_json_steps,
        "total_steps": total_steps,
        "grounding_errors": grounding_errors,
        "retry_used": retry_used,
        "failure_type": failure_type,
    }


def compute_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    total_runs = len(records)
    success_runs = sum(1 for record in records if record["success"])
    validated_success_runs = sum(1 for record in records if record.get("validated_success") is True)
    validator_records = [record for record in records if record.get("validator_evaluator")]
    binary_validator_records = [record for record in validator_records if isinstance(record.get("validator_success"), bool)]
    validator_review_runs = sum(1 for record in validator_records if record.get("validator_needs_review") is True)
    validator_success_runs = sum(1 for record in binary_validator_records if record.get("validator_success") is True)
    total_steps = sum(record["total_steps"] for record in records)
    valid_json_steps = sum(record["action_json_valid_steps"] for record in records)
    grounding_errors = sum(record["grounding_errors"] for record in records)
    retry_runs = [record for record in records if record["retry_used"]]
    retry_success_runs = sum(1 for record in retry_runs if record["success"])
    failure_counts = Counter(record["failure_type"] for record in records if record["failure_type"])

    return {
        "total_runs": total_runs,
        "success_runs": success_runs,
        "task_success_rate": _safe_rate(success_runs, total_runs),
        "validated_success_runs": validated_success_runs,
        "validated_success_rate": _safe_rate(validated_success_runs, total_runs),
        "validator_evaluated_runs": len(validator_records),
        "validator_binary_runs": len(binary_validator_records),
        "validator_review_runs": validator_review_runs,
        "validator_success_runs": validator_success_runs,
        "validator_success_rate": _safe_rate(validator_success_runs, len(binary_validator_records)),
        "average_steps": _safe_average([record["steps_used"] for record in records]),
        "average_time": _safe_average([record["duration_seconds"] for record in records]),
        "action_json_validity_rate": _safe_rate(valid_json_steps, total_steps),
        "grounding_error_rate": _safe_rate(grounding_errors, total_steps),
        "correction_retry_success_rate": _safe_rate(retry_success_runs, len(retry_runs)),
        "retry_runs": len(retry_runs),
        "failure_type_distribution": {failure_type: failure_counts.get(failure_type, 0) for failure_type in FAILURE_TYPES},
    }


def classify_failure(summary: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    final_status = str(summary.get("final_status") or "").lower()
    final_reason = _summary_final_reason(summary).lower()
    errors = summary.get("errors") or []

    if _mentions_environment_error(final_reason):
        return "website"
    if final_status == "fatal_error":
        return "website" if _mentions_website_error(final_reason) else "execution"
    if any(_mentions_website_error(str(error.get("error", ""))) for error in errors):
        return "website"
    if any(_is_grounding_error(step) for step in steps):
        return "perception"
    if any(_is_execution_error(step) for step in steps):
        return "execution"
    if any(_is_verification_failure(step) for step in steps):
        return "verification"
    if final_status in {"stuck", "max_steps_exceeded", "model_error"}:
        return "planning"
    return "planning"


def _load_step_record(step_dir: Path) -> dict[str, Any]:
    return {
        "step_dir": str(step_dir),
        "parsed_action": load_json(step_dir / "parsed_action.json", {}),
        "execution_result": load_json(step_dir / "execution_result.json", {}),
        "progress_check": load_json(step_dir / "progress_check.json", {}),
        "final_verification": load_json(step_dir / "final_verification.json", {}),
    }


def _action_json_valid(step: dict[str, Any]) -> bool:
    parsed_action = step.get("parsed_action") or {}
    execution_result = step.get("execution_result") or {}
    return bool(parsed_action.get("action")) and not execution_result.get("model_parse_error") and not execution_result.get(
        "fallback_action_used"
    )


def _is_grounding_error(step: dict[str, Any]) -> bool:
    execution_result = step.get("execution_result") or {}
    error = str(execution_result.get("error") or "").lower()
    error_type = str(execution_result.get("error_type") or "").lower()
    return error_type == "validation_error" and any(
        term in error for term in ("target_id", "visible", "enabled", "candidate")
    )


def _is_execution_error(step: dict[str, Any]) -> bool:
    execution_result = step.get("execution_result") or {}
    if execution_result.get("ok"):
        return False
    error_type = str(execution_result.get("error_type") or "").lower()
    if not error_type:
        return False
    return error_type not in {"model_output_error", "model_client_error", "validation_error"}


def _is_verification_failure(step: dict[str, Any]) -> bool:
    final_verification = step.get("final_verification") or {}
    reason = str(final_verification.get("reason") or "").lower()
    final_action = str((final_verification.get("evidence") or {}).get("final_action") or "").lower()
    return final_action in {"answer", "done"} and not final_verification.get("success") and "uncertain" in reason


def _mentions_website_error(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("timeout", "net::", "navigation", "captcha", "blocked", "closed"))


def _mentions_environment_error(text: str) -> bool:
    lowered = text.lower()
    return any(
        term in lowered
        for term in (
            "insufficient_user_quota",
            "quota is not enough",
            "用户额度不足",
            "http 403",
            "rate limit",
            "chat completion request failed",
        )
    )


def _summary_success(summary: dict[str, Any]) -> bool:
    if summary.get("success"):
        return True
    if (
        summary.get("report_error_type") == "final_report_generation_failed"
        or summary.get("blocked_status") == "final_report_generation_failed"
    ):
        return str(summary.get("pre_report_final_status") or "").lower() == "success"
    return False


def _summary_final_reason(summary: dict[str, Any]) -> str:
    if (
        summary.get("report_error_type") == "final_report_generation_failed"
        or summary.get("blocked_status") == "final_report_generation_failed"
    ):
        return str(summary.get("pre_report_final_reason") or summary.get("final_reason") or "")
    return str(summary.get("final_reason") or "")


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _safe_average(values: list[int | float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)
