from __future__ import annotations

import hashlib
import json
import os
import traceback
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agents.final_verifier import FinalVerifier, default_final_verification
from agents.progress_checker import ProgressChecker
from agents.prompts import build_react_messages, messages_to_prompt_text
from agents.react_agent import SingleStepReactAgent, safe_wait_action
from agents.report_builder import generate_final_html_report, generate_partial_final_answer
from agents.task_checklist import (
    apply_checklist_updates,
    assess_task_checklist,
    build_task_checklist,
    checklist_hint,
    generate_final_answer_from_evidence,
    should_answer_from_assessment,
)
from browser.actions import CandidateElement
from browser.cdp_env import CdpBrowserEnv
from browser.playwright_env import PlaywrightEnv
from browser.web_access_env import WebAccessEnv
from models.base_client import ModelClientError, load_env_file
from models.deepseek_client import DeepSeekClient
from models.gemini_client import GeminiClient
from models.openai_compatible_client import OpenAICompatibleClient
from perception.som_marker import draw_set_of_marks


TRACE_DIR = Path("traces/demo_run")
TASK_TRACE_DIR = Path("traces/task_run")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CONTROL_FILE = "runtime_control.json"


class MockAgent:
    def next_action(self, candidates: list[CandidateElement]) -> dict[str, Any]:
        for candidate in candidates:
            if candidate.visible and candidate.enabled and candidate.role == "textbox":
                return {
                    "action": "type",
                    "target_id": candidate.element_id,
                    "text": "web-mmagent",
                    "direction": "down",
                    "key": "Enter",
                }

        return {
            "action": "scroll",
            "target_id": None,
            "text": "",
            "direction": "down",
            "key": "Enter",
        }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _summary_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def run_demo(url: str, trace_dir: Path = TRACE_DIR) -> dict[str, Any]:
    trace_dir.mkdir(parents=True, exist_ok=True)
    agent = MockAgent()

    try:
        with PlaywrightEnv() as env:
            env.open_url(url)

            screenshot_path = trace_dir / "screenshot.png"
            env.screenshot(screenshot_path)

            candidates = env.candidates()
            candidates_data = [candidate.model_dump() for candidate in candidates]
            write_json(trace_dir / "candidates.json", candidates_data)

            action = agent.next_action(candidates)
            write_json(trace_dir / "action.json", action)

            execution_result = env.execute_action(action, candidates)
            write_json(trace_dir / "execution_result.json", execution_result)

            return {
                "trace_dir": _summary_path(trace_dir),
                "screenshot": str(screenshot_path),
                "candidate_count": len(candidates),
                "action": action,
                "execution_result": execution_result,
            }
    except Exception as exc:
        execution_result = {
            "ok": False,
            "action": None,
            "url_before": url,
            "url_after": None,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        write_json(trace_dir / "execution_result.json", execution_result)
        return {
            "trace_dir": _summary_path(trace_dir),
            "screenshot": str(trace_dir / "screenshot.png"),
            "candidate_count": 0,
            "action": None,
            "execution_result": execution_result,
        }


def run_task(
    start_url: str,
    instruction: str,
    max_steps: int,
    provider: str,
    model: str | None = None,
    trace_dir: Path = TASK_TRACE_DIR,
    headless: bool = True,
    run_id: str | None = None,
    task_metadata: dict[str, Any] | None = None,
    browser_backend: str = "playwright",
    web_access_browser: str | None = None,
    ask_user_on_failure: bool = False,
    include_screenshot_images: bool | None = None,
    start_step_index: int = 0,
    existing_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    load_env_file()
    started_at = datetime.now()
    start_time = time.perf_counter()
    run_id = run_id or make_run_id(start_url)
    run_dir = trace_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    previous_summary = _load_json(run_dir / "run_summary.json") if start_step_index > 0 else {}
    previous_config = _load_json(run_dir / "run_config.json") if start_step_index > 0 else {}
    original_started_at = str(
        previous_summary.get("started_at")
        or previous_config.get("started_at")
        or started_at.isoformat(timespec="seconds")
    )
    previous_duration = float(previous_summary.get("duration_seconds") or 0.0) if start_step_index > 0 else 0.0
    cumulative_max_steps = start_step_index + max_steps
    requested_model = str(model or previous_config.get("model") or "").strip() or None
    if include_screenshot_images is None and "include_screenshot_images" in previous_config:
        include_screenshot_images = bool(previous_config.get("include_screenshot_images"))
    if include_screenshot_images is None:
        include_screenshot_images = provider in {"openai", "gemini", "cdp"}

    run_config = {
        **previous_config,
        "run_id": run_id,
        "start_url": start_url,
        "instruction": instruction,
        "max_steps": cumulative_max_steps,
        "step_chunk_size": max_steps,
        "provider": provider,
        "model": requested_model or "",
        "browser_backend": browser_backend,
        "web_access_browser": web_access_browser,
        "ask_user_on_failure": ask_user_on_failure,
        "include_screenshot_images": include_screenshot_images,
        "headless": headless,
        "task_metadata": task_metadata or {},
        "started_at": original_started_at,
        "last_continued_at": started_at.isoformat(timespec="seconds") if start_step_index > 0 else None,
    }
    write_json(run_dir / "run_config.json", run_config)

    summary: dict[str, Any] = {
        **previous_summary,
        "run_id": run_id,
        "success": False,
        "final_status": "not_started",
        "steps_used": start_step_index,
        "trace_dir": _summary_path(run_dir),
        "instruction": instruction,
        "final_reason": "",
        "final_answer": "",
        "final_report_path": previous_summary.get("final_report_path", "") if start_step_index > 0 else "",
        "final_report_error": "",
        "final_url": start_url,
        "errors": previous_summary.get("errors", []) if isinstance(previous_summary.get("errors"), list) else [],
        "started_at": original_started_at,
        "ended_at": None,
        "duration_seconds": previous_duration,
        "task_metadata": task_metadata or {},
        "provider": provider,
        "model": requested_model or "",
        "browser_backend": browser_backend,
        "include_screenshot_images": include_screenshot_images,
    }

    def flush_running_summary() -> None:
        snapshot = dict(summary)
        if snapshot.get("final_status") == "not_started":
            snapshot["final_status"] = "running"
        snapshot["ended_at"] = None
        snapshot["duration_seconds"] = round(previous_duration + time.perf_counter() - start_time, 3)
        snapshot["updated_at"] = datetime.now().isoformat(timespec="seconds")
        write_json(run_dir / "run_summary.json", snapshot)

    history: list[dict[str, Any]] = existing_history if existing_history is not None else _load_history_from_trace(run_dir)
    base_human_hints = _human_hints_from_metadata(task_metadata or {})
    progress_checker = ProgressChecker()
    final_verifier = FinalVerifier()
    consecutive_stuck = 0
    auto_hints: list[str] = []
    checklist_recovery_count = 0

    try:
        client = create_model_client(provider, model=requested_model)
        model_name = getattr(getattr(client, "config", None), "model", requested_model or "")
        run_config["model"] = model_name
        summary["model"] = model_name
        write_json(run_dir / "run_config.json", run_config)
        task_checklist = _load_json(run_dir / "checklist.json") if start_step_index > 0 else {}
        if not task_checklist:
            task_checklist = build_task_checklist(client, instruction)
        summary["checklist"] = task_checklist
        write_json(run_dir / "checklist.json", task_checklist)
        flush_running_summary()
        agent = SingleStepReactAgent(client)

        with create_browser_env(
            provider=provider,
            browser_backend=browser_backend,
            headless=headless,
            web_access_browser=web_access_browser,
        ) as env:
            env.open_url(start_url)

            for step_index in range(start_step_index, start_step_index + max_steps):
                runtime_control = _load_runtime_control(run_dir)
                if _runtime_stop_requested(runtime_control):
                    _mark_user_stopped(summary, runtime_control)
                    break
                human_hints = _merge_hints(
                    base_human_hints,
                    _runtime_hints_from_control(runtime_control),
                    auto_hints,
                    [checklist_hint(task_checklist)],
                )

                step_dir = run_dir / f"step_{step_index:03d}"
                step_dir.mkdir(parents=True, exist_ok=True)
                summary["final_status"] = "running"
                summary["current_step_index"] = step_index
                summary["active_step"] = step_dir.name
                summary["steps_used"] = step_index
                flush_running_summary()

                try:
                    step_record = _run_one_step(
                        env=env,
                        agent=agent,
                        instruction=instruction,
                        provider=provider,
                        model=model_name,
                        browser_backend=browser_backend,
                        include_screenshot_images=include_screenshot_images,
                        step_index=step_index,
                        step_dir=step_dir,
                        history=history,
                        human_hints=human_hints,
                        progress_checker=progress_checker,
                        final_verifier=final_verifier,
                    )
                except Exception as exc:
                    execution_result = {
                        "ok": False,
                        "success": False,
                        "action": None,
                        "url_before": _current_url(env, start_url),
                        "url_after": _current_url(env, start_url),
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                    write_json(step_dir / "execution_result.json", execution_result)
                    summary["errors"].append(execution_result)
                    summary["steps_used"] = step_index + 1
                    summary["active_step"] = ""
                    summary["final_url"] = execution_result["url_after"]
                    if ask_user_on_failure:
                        _finish_with_partial_failure(
                            client=client,
                            run_dir=run_dir,
                            summary=summary,
                            instruction=instruction,
                            blocked_status="step_error",
                            current_url=execution_result["url_after"],
                            detail=str(exc),
                        )
                    else:
                        summary["final_status"] = "step_error"
                        summary["final_reason"] = str(exc)
                    break

                history.append(step_record)
                summary["steps_used"] = step_index + 1
                summary["active_step"] = ""
                summary["final_url"] = step_record.get("post_info", {}).get("url", summary["final_url"])
                checklist_updates = step_record.get("model_action", {}).get("checklist_updates") or []
                if checklist_updates:
                    task_checklist = apply_checklist_updates(
                        task_checklist,
                        checklist_updates,
                        current_url=summary["final_url"],
                    )
                    summary["checklist"] = task_checklist
                    summary["checklist_completion_ratio"] = task_checklist.get("overall_completion_ratio", 0.0)
                    summary["checklist_research_completion_ratio"] = task_checklist.get("research_completion_ratio", 0.0)
                    summary["checklist_recommended_next_step"] = task_checklist.get("recommended_next_step", "")
                    write_json(run_dir / "checklist.json", task_checklist)
                    write_json(step_dir / "checklist_updates.json", checklist_updates)
                    write_json(step_dir / "checklist_snapshot.json", task_checklist)
                flush_running_summary()
                runtime_control = _load_runtime_control(run_dir)
                if _runtime_stop_requested(runtime_control):
                    _mark_user_stopped(summary, runtime_control)
                    break

                progress = step_record["progress_check"]
                final_verification = step_record["final_verification"]
                model_action = step_record.get("model_action", {})
                action_name = model_action.get("action")
                action_text = str(model_action.get("text") or "").strip()

                if progress.get("stuck"):
                    consecutive_stuck += 1
                else:
                    consecutive_stuck = 0

                checklist_blocked_reason = _checklist_audit_reason(progress, consecutive_stuck, progress_checker)
                if ask_user_on_failure and checklist_blocked_reason:
                    try:
                        assessment = assess_task_checklist(
                            client=client,
                            run_dir=run_dir,
                            instruction=instruction,
                            current_checklist=task_checklist,
                            blocked_reason=checklist_blocked_reason,
                        )
                    except Exception as exc:
                        assessment = task_checklist
                        assessment["checklist_error"] = f"{exc.__class__.__name__}: {exc}"
                    task_checklist = assessment
                    summary["checklist"] = task_checklist
                    summary["checklist_completion_ratio"] = task_checklist.get("overall_completion_ratio", 0.0)
                    summary["checklist_research_completion_ratio"] = task_checklist.get("research_completion_ratio", 0.0)
                    summary["checklist_recommended_next_step"] = task_checklist.get("recommended_next_step", "")
                    write_json(run_dir / "checklist.json", task_checklist)
                    write_json(step_dir / "checklist_assessment.json", task_checklist)

                    if should_answer_from_assessment(task_checklist):
                        summary["success"] = True
                        summary["final_status"] = "success"
                        summary["blocked_status"] = "redundant_evidence_loop"
                        summary["final_reason"] = (
                            "已经收集到足够证据；检测到后续浏览开始重复，因此进入最终答案生成流程。"
                        )
                        try:
                            summary["final_answer"] = generate_final_answer_from_evidence(
                                client=client,
                                run_dir=run_dir,
                                instruction=instruction,
                                checklist_assessment=task_checklist,
                            )
                        except Exception as exc:
                            summary["success"] = False
                            summary["final_status"] = "fail"
                            summary["final_reason"] = f"final_answer_from_evidence_error: {exc}"
                            summary["final_answer"] = _fallback_partial_failure_answer(
                                summary,
                                "final_answer_from_evidence_error",
                                summary["final_url"],
                                str(exc),
                            )
                        _write_final_html_report(
                            client=client,
                            run_dir=run_dir,
                            instruction=instruction,
                            summary=summary,
                        )
                        break

                    recommended_next_step = str(task_checklist.get("recommended_next_step") or "").lower()
                    suggested_hint = str(task_checklist.get("suggested_agent_hint") or "").strip()
                    if recommended_next_step == "continue" and suggested_hint and checklist_recovery_count < 2:
                        auto_hints = [f"Checklist recovery hint: {suggested_hint}"]
                        checklist_recovery_count += 1
                        consecutive_stuck = 0
                        continue

                    _finish_with_partial_failure(
                        client=client,
                        run_dir=run_dir,
                        summary=summary,
                        instruction=instruction,
                        blocked_status="redundant_evidence_loop",
                        current_url=summary["final_url"],
                        detail=checklist_blocked_reason,
                    )
                    break

                if action_name == "ask_user":
                    summary["final_status"] = "needs_user_input"
                    summary["final_reason"] = action_text or "Agent needs user input"
                    summary["final_answer"] = action_text
                    break

                if final_verification.get("success"):
                    summary["success"] = True
                    summary["final_status"] = "success"
                    summary["final_reason"] = final_verification.get("reason", "")
                    if action_name in {"answer", "done"} and action_text:
                        summary["final_answer"] = action_text
                        _write_final_html_report(
                            client=client,
                            run_dir=run_dir,
                            instruction=instruction,
                            summary=summary,
                        )
                    break

                if step_record.get("model_error"):
                    detail = step_record["execution_result"].get("error", "")
                    if ask_user_on_failure:
                        _finish_with_partial_failure(
                            client=client,
                            run_dir=run_dir,
                            summary=summary,
                            instruction=instruction,
                            blocked_status="model_error",
                            current_url=summary["final_url"],
                            detail=detail,
                        )
                    else:
                        summary["final_status"] = "model_error"
                        summary["final_reason"] = detail
                    break

                if consecutive_stuck >= progress_checker.stuck_threshold:
                    detail = progress.get("reason", "stuck")
                    blocked_status = "needs_user_help" if progress.get("needs_user_help") else "stuck"
                    if ask_user_on_failure:
                        _finish_with_partial_failure(
                            client=client,
                            run_dir=run_dir,
                            summary=summary,
                            instruction=instruction,
                            blocked_status=blocked_status,
                            current_url=summary["final_url"],
                            detail=detail,
                        )
                    else:
                        summary["final_status"] = "stuck"
                        summary["final_reason"] = detail
                    break
            else:
                detail = f"reached current step budget={max_steps}"
                if ask_user_on_failure:
                    _request_human_help(
                        summary=summary,
                        blocked_status="max_steps_exceeded",
                        instruction=instruction,
                        current_url=summary["final_url"],
                        detail=detail,
                    )
                else:
                    summary["final_status"] = "max_steps_exceeded"
                    summary["final_reason"] = detail
    except Exception as exc:
        summary["errors"].append(
            {
                "error_type": exc.__class__.__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        if ask_user_on_failure:
            try:
                _finish_with_partial_failure(
                    client=client,
                    run_dir=run_dir,
                    summary=summary,
                    instruction=instruction,
                    blocked_status="fatal_error",
                    current_url=summary["final_url"],
                    detail=str(exc),
                )
            except Exception:
                _request_human_help(
                    summary=summary,
                    blocked_status="fatal_error",
                    instruction=instruction,
                    current_url=summary["final_url"],
                    detail=str(exc),
                )
        else:
            summary["final_status"] = "fatal_error"
            summary["final_reason"] = str(exc)

    if summary["final_status"] == "not_started":
        summary["final_status"] = "max_steps_exceeded" if max_steps <= 0 else "unknown"
        summary["final_reason"] = "no steps were executed"

    _apply_runtime_control_to_summary(summary, run_dir)
    ended_at = datetime.now()
    summary["ended_at"] = ended_at.isoformat(timespec="seconds")
    summary["duration_seconds"] = round(previous_duration + time.perf_counter() - start_time, 3)
    write_json(run_dir / "run_summary.json", summary)
    return summary


def _run_one_step(
    env: Any,
    agent: SingleStepReactAgent,
    instruction: str,
    provider: str,
    model: str,
    browser_backend: str,
    include_screenshot_images: bool,
    step_index: int,
    step_dir: Path,
    history: list[dict[str, Any]],
    human_hints: list[str],
    progress_checker: ProgressChecker,
    final_verifier: FinalVerifier,
) -> dict[str, Any]:
    screenshot_path = step_dir / "screenshot.png"
    marked_screenshot_path = step_dir / "marked_screenshot.png"
    accessibility_tree_path = step_dir / "accessibility_tree.txt"
    page_text_path = step_dir / "page_text.txt"
    candidates_path = step_dir / "candidates.json"
    prompt_path = step_dir / "prompt.txt"
    raw_response_path = step_dir / "raw_response.txt"
    parsed_action_path = step_dir / "parsed_action.json"
    execution_result_path = step_dir / "execution_result.json"
    progress_check_path = step_dir / "progress_check.json"
    final_verification_path = step_dir / "final_verification.json"

    env.screenshot(screenshot_path)
    accessibility_tree_text = env.accessibility_snapshot()
    write_text(accessibility_tree_path, accessibility_tree_text)
    candidates = env.candidates()
    page_text = _page_text(env, candidates)
    write_text(page_text_path, page_text)
    write_json(candidates_path, [candidate.model_dump() for candidate in candidates])
    draw_set_of_marks(
        screenshot_path=screenshot_path,
        candidates=candidates,
        output_path=marked_screenshot_path,
    )

    previous_step_info = {
        "url": _current_url(env, ""),
        "screenshot_hash": _file_sha256(screenshot_path),
        "candidate_count": len(candidates),
        "candidate_text_signature": _candidate_text_signature(candidates),
    }

    messages = build_react_messages(
        task=instruction,
        current_url=previous_step_info["url"],
        candidates=candidates,
        action_history=_history_for_prompt(history),
        human_hints=human_hints,
        screenshot_path=screenshot_path,
        marked_screenshot_path=marked_screenshot_path,
        accessibility_tree_text=accessibility_tree_text,
        page_text=page_text,
        include_screenshot_image=include_screenshot_images,
    )
    write_text(prompt_path, messages_to_prompt_text(messages))

    model_error = False
    try:
        agent_result = agent.decide(messages)
        parsed_action = agent_result.parsed_action
        raw_responses = agent_result.raw_responses
        parse_error = agent_result.parse_error
        fallback_used = agent_result.fallback_used
    except ModelClientError as exc:
        model_error = True
        parsed_action = safe_wait_action(str(exc))
        raw_responses = []
        parse_error = str(exc)
        fallback_used = True

    write_text(raw_response_path, _format_raw_responses(raw_responses))
    write_json(parsed_action_path, parsed_action.model_dump())

    execution_result = env.execute_action(parsed_action.to_browser_action(), candidates)
    execution_result.update(
        {
            "success": bool(execution_result.get("ok")),
            "fallback_action_used": fallback_used,
            "model_parse_error": parse_error,
            "model_provider": provider,
            "model": model,
            "browser_backend": browser_backend,
            "include_screenshot_images": include_screenshot_images,
            "model_response_attempts": len(raw_responses),
            "model_retry_used": len(raw_responses) > 1,
        }
    )

    if fallback_used:
        execution_result.update(
            {
                "ok": False,
                "success": False,
                "error_type": "model_output_error" if not model_error else "model_client_error",
                "error": parse_error,
            }
        )

    write_json(execution_result_path, execution_result)

    post_candidates = env.candidates()
    current_step_info = {
        "url": _current_url(env, previous_step_info["url"]),
        "screenshot_hash": _page_screenshot_sha256(env),
        "candidate_count": len(post_candidates),
        "candidate_text_signature": _candidate_text_signature(post_candidates),
    }

    browser_action = parsed_action.to_browser_action()
    progress_check = progress_checker.check(
        previous_step_info=previous_step_info,
        current_step_info=current_step_info,
        action=browser_action,
        execution_result=execution_result,
        history=history,
    )
    write_json(progress_check_path, progress_check)

    if parsed_action.action == "ask_user":
        final_verification = {
            "success": False,
            "reason": "agent requested user input",
            "evidence": {
                "final_action": "ask_user",
                "answer_text": parsed_action.text or "",
            },
        }
    elif parsed_action.action in {"answer", "done"}:
        final_verification = final_verifier.verify(
            instruction=instruction,
            final_action=parsed_action.model_dump(),
            current_url=current_step_info["url"],
            page_text=f"{_page_text(env, post_candidates)}\n{accessibility_tree_text}\n{_history_evidence_text(history)}",
            action_history=history,
            execution_result=execution_result,
        )
    else:
        final_verification = default_final_verification(parsed_action.model_dump())
    write_json(final_verification_path, final_verification)

    return {
        "step_index": step_index,
        "action": browser_action,
        "model_action": parsed_action.model_dump(),
        "execution_result": execution_result,
        "progress_check": progress_check,
        "final_verification": final_verification,
        "pre_info": previous_step_info,
        "post_info": current_step_info,
        "page_text_excerpt": _compact_evidence_excerpt(page_text, 900),
        "model_error": model_error,
    }



def _write_final_html_report(
    client: Any,
    run_dir: Path,
    instruction: str,
    summary: dict[str, Any],
) -> None:
    final_answer = str(summary.get("final_answer") or "").strip()
    if not final_answer:
        return
    if os.getenv("WEB_MMAGENT_SKIP_FINAL_REPORT", "").strip().lower() in {"1", "true", "yes", "y"}:
        summary["report_status"] = "skipped"
        summary["final_report_path"] = ""
        summary["final_report_error"] = "final HTML report generation skipped by WEB_MMAGENT_SKIP_FINAL_REPORT"
        write_json(run_dir / "run_summary.json", summary)
        return
    report_path = run_dir / "final_report.html"
    errors: list[str] = []
    max_attempts = 6
    client = _client_with_report_config(client, timeout_seconds=45, max_retries=1)
    for attempt in range(1, max_attempts + 1):
        summary["report_status"] = "running"
        summary["report_attempt"] = attempt
        summary["report_max_attempts"] = max_attempts
        summary["final_report_error"] = "; ".join(errors[-2:])
        write_json(run_dir / "run_summary.json", summary)
        try:
            report_html = generate_final_html_report(
                client=client,
                run_dir=run_dir,
                instruction=instruction,
                final_answer=final_answer,
                summary=summary,
            )
            if not report_html.strip():
                raise ValueError("report model returned empty HTML")
            write_text(report_path, report_html)
            summary["final_report_path"] = _summary_path(report_path)
            summary["final_report_error"] = ""
            return
        except Exception as exc:
            errors.append(f"attempt {attempt}/{max_attempts}: {exc.__class__.__name__}: {exc}")
            if attempt < max_attempts:
                time.sleep(min(attempt, 5))

    if report_path.exists():
        report_path.unlink()
    detail = "; ".join(errors[-3:]) or "unknown report generation error"
    summary["pre_report_final_status"] = summary.get("final_status")
    summary["pre_report_final_reason"] = summary.get("final_reason")
    summary["report_status"] = "failed"
    summary["report_error_type"] = "final_report_generation_failed"
    summary["final_report_error"] = detail
    summary["final_report_path"] = ""


def _client_with_report_config(client: Any, timeout_seconds: int, max_retries: int) -> Any:
    config = getattr(client, "config", None)
    if config is None or not hasattr(config, "timeout_seconds"):
        return client
    try:
        updates = {"timeout_seconds": timeout_seconds}
        if hasattr(config, "max_retries"):
            updates["max_retries"] = max_retries
        client.config = replace(config, **updates)
    except Exception:
        pass
    return client


def _finish_with_partial_failure(
    client: Any,
    run_dir: Path,
    summary: dict[str, Any],
    instruction: str,
    blocked_status: str,
    current_url: str,
    detail: str,
) -> None:
    reason = f"{blocked_status}: {detail}" if detail else blocked_status
    summary["success"] = False
    summary["final_status"] = "fail"
    summary["blocked_status"] = blocked_status
    summary["final_url"] = current_url or summary.get("final_url") or ""
    summary["final_reason"] = reason
    try:
        answer = generate_partial_final_answer(
            client=client,
            run_dir=run_dir,
            instruction=instruction,
            summary=summary,
            blocked_status=blocked_status,
            detail=detail,
        )
    except Exception as exc:
        answer = _fallback_partial_failure_answer(summary, blocked_status, current_url, detail)
        summary["partial_answer_error"] = f"{exc.__class__.__name__}: {exc}"
    summary["final_answer"] = answer
    _write_final_html_report(
        client=client,
        run_dir=run_dir,
        instruction=instruction,
        summary=summary,
    )


def _checklist_audit_reason(
    progress: dict[str, Any],
    consecutive_stuck: int,
    progress_checker: ProgressChecker,
) -> str:
    signals = progress.get("signals") if isinstance(progress.get("signals"), dict) else {}
    reason = str(progress.get("reason") or "").strip()
    if signals.get("action_loop"):
        return reason or "navigation/action loop detected"
    if signals.get("repeated_action"):
        return reason or "same action repeated"
    if signals.get("navigation_landed_elsewhere"):
        return reason or "navigation landed on a different state than requested"
    if consecutive_stuck >= progress_checker.stuck_threshold:
        return reason or "page state did not change for multiple consecutive steps"
    return ""


def _fallback_partial_failure_answer(
    summary: dict[str, Any],
    blocked_status: str,
    current_url: str,
    detail: str,
) -> str:
    return "\n".join(
        part
        for part in [
            "任务没有完整完成，但系统已经保留当前阶段性结果。",
            f"失败状态：{blocked_status}",
            f"当前页面：{current_url or summary.get('final_url') or '未知'}",
            f"原因：{detail or '没有额外错误信息'}",
            "可在 HTML 报告和 trace 中查看已经访问过的页面、截图和动作轨迹。",
        ]
        if part
    )


def _request_human_help(
    summary: dict[str, Any],
    blocked_status: str,
    instruction: str,
    current_url: str,
    detail: str,
) -> None:
    if blocked_status == "max_steps_exceeded":
        message = "\n".join(
            part
            for part in [
                "本轮 15 步已经执行完，但任务还没有完成。",
                f"当前页面：{current_url or '未知'}",
                "如果你同意，我会从当前页面继续执行下一轮 15 步。",
                "你也可以直接给建议，例如“继续，但不要再用搜索引擎，先关闭弹窗/用页面筛选控件”。我会把建议作为下一轮高优先级提示。",
                "请回复“继续/同意”来追加 15 步；如果回复“停止/不同意”，我就结束这个任务。",
            ]
            if part
        )
    else:
        message = "\n".join(
            part
            for part in [
                "我这里没有直接判失败，而是先停下来请你帮我确认下一步。",
                f"卡住类型：{blocked_status}",
                f"当前页面：{current_url or '未知'}",
                f"具体原因：{detail or '没有额外错误信息'}",
                "你可以告诉我：换哪个网站/搜索词，点哪个按钮，是否等待或跳过当前平台，或者把你在页面上看到的关键信息发给我。我会根据你的回复继续重新规划。",
            ]
            if part
        )
    summary["success"] = False
    summary["final_status"] = "needs_user_input"
    summary["blocked_status"] = blocked_status
    summary["final_reason"] = message
    summary["final_answer"] = message


def _load_history_from_trace(run_dir: Path) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for step_dir in sorted(path for path in run_dir.glob("step_*") if path.is_dir()):
        try:
            step_index = int(step_dir.name.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        model_action = _load_json(step_dir / "parsed_action.json")
        execution_result = _load_json(step_dir / "execution_result.json")
        progress_check = _load_json(step_dir / "progress_check.json")
        final_verification = _load_json(step_dir / "final_verification.json")
        if not model_action and not execution_result:
            continue
        history.append(
            {
                "step_index": step_index,
                "action": execution_result.get("action") or model_action,
                "model_action": model_action,
                "execution_result": execution_result,
                "progress_check": progress_check,
                "final_verification": final_verification,
                "model_error": False,
            }
        )
    return history


def create_model_client(provider: str, model: str | None = None) -> OpenAICompatibleClient:
    if provider == "deepseek":
        client = DeepSeekClient.from_env()
    if provider == "openai":
        client = OpenAICompatibleClient.from_env()
    if provider == "gemini":
        client = GeminiClient.from_env()
    if provider == "cdp":
        # CDP 模式使用 Gemini 视觉模型 + 真实浏览器
        client = GeminiClient.from_env()
    if provider in {"deepseek", "openai", "gemini", "cdp"}:
        if model:
            client.config = replace(client.config, model=model)
        return client
    raise ValueError(f"unsupported provider: {provider}")



def create_browser_env(
    provider: str,
    browser_backend: str,
    headless: bool,
    web_access_browser: str | None = None,
) -> Any:
    timeout_ms = _browser_timeout_ms()
    if browser_backend == "web_access":
        return WebAccessEnv(headless=headless, browser=web_access_browser, timeout_ms=timeout_ms)
    if browser_backend != "playwright":
        raise ValueError(f"unsupported browser_backend: {browser_backend}")
    if provider == "cdp":
        return CdpBrowserEnv(headless=headless, timeout_ms=timeout_ms)
    return PlaywrightEnv(headless=headless, timeout_ms=timeout_ms)


def _browser_timeout_ms() -> int:
    raw_value = os.getenv("WEB_MMAGENT_BROWSER_TIMEOUT_MS", "").strip()
    if not raw_value:
        return 30_000
    try:
        timeout_ms = int(raw_value)
    except ValueError:
        return 30_000
    return max(5_000, timeout_ms)

def make_run_id(start_url: str) -> str:
    hostname = urlparse(start_url).hostname or "site"
    slug = "".join(char if char.isalnum() else "_" for char in hostname.split(".")[0]).strip("_") or "site"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{slug}"


def _history_for_prompt(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact_history: list[dict[str, Any]] = []
    for item in history[-18:]:
        evidence_excerpt = str(item.get("page_text_excerpt") or "").strip()
        compact_history.append(
            {
                "step": item.get("step_index"),
                "action": item.get("model_action"),
                "execution_ok": item.get("execution_result", {}).get("ok"),
                "url_before": item.get("execution_result", {}).get("url_before"),
                "url_after": item.get("execution_result", {}).get("url_after"),
                "progress": item.get("progress_check", {}).get("reason"),
                "progress_signals": item.get("progress_check", {}).get("signals"),
                "evidence_excerpt": evidence_excerpt[:900] if evidence_excerpt else "",
            }
        )
    return compact_history


def _history_evidence_text(history: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    seen: set[str] = set()
    for item in history:
        url = str(item.get("post_info", {}).get("url") or item.get("execution_result", {}).get("url_after") or "").strip()
        excerpt = str(item.get("page_text_excerpt") or "").strip()
        if not excerpt:
            continue
        key = url or f"step-{item.get('step_index')}"
        if key in seen:
            continue
        seen.add(key)
        chunks.append(
            "Step {step} URL: {url}\nEvidence excerpt: {excerpt}".format(
                step=item.get("step_index"),
                url=url or "(unknown)",
                excerpt=excerpt[:900],
            )
        )
    if not chunks:
        return ""
    return "\n\n# Previously Observed Evidence\n" + "\n\n".join(chunks[-28:])


def _compact_evidence_excerpt(text: str, max_chars: int) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]} ... [truncated {len(compact) - max_chars} chars]"


def _human_hints_from_metadata(metadata: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    raw_hints = metadata.get("human_hints")
    if isinstance(raw_hints, list):
        for item in raw_hints:
            text = str(item).strip()
            if text:
                hints.append(text)

    continuation = metadata.get("continuation")
    if isinstance(continuation, dict):
        hint = str(continuation.get("user_hint") or "").strip()
        if hint:
            hints.append(hint)

    deduped: list[str] = []
    for hint in hints:
        if hint not in deduped:
            deduped.append(hint)
    return deduped[-5:]


def _load_runtime_control(run_dir: Path) -> dict[str, Any]:
    path = run_dir / RUNTIME_CONTROL_FILE
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_stop_requested(control: dict[str, Any]) -> bool:
    return bool(control.get("stop_requested"))


def _runtime_hints_from_control(control: dict[str, Any]) -> list[str]:
    raw_hints = control.get("human_hints")
    if not isinstance(raw_hints, list):
        return []
    return [str(item).strip() for item in raw_hints if str(item).strip()][-5:]


def _runtime_chat_history(control: dict[str, Any]) -> list[dict[str, str]]:
    raw_history = control.get("chat_history")
    if not isinstance(raw_history, list):
        return []
    history: list[dict[str, str]] = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        role = "user" if str(item.get("role") or "").lower() in {"user", "human"} else "agent"
        text = str(item.get("text") or "").strip()
        if text:
            history.append({"role": role, "text": text})
    return history


def _merge_hints(*hint_lists: list[str]) -> list[str]:
    merged: list[str] = []
    for hints in hint_lists:
        for hint in hints:
            text = str(hint).strip()
            if text and text not in merged:
                merged.append(text)
    return merged[-5:]


def _mark_user_stopped(summary: dict[str, Any], control: dict[str, Any]) -> None:
    reason = str(control.get("stop_reason") or "用户请求停止当前任务。").strip()
    summary["success"] = False
    summary["final_status"] = "user_stopped"
    summary["final_reason"] = reason
    summary["final_answer"] = reason


def _apply_runtime_control_to_summary(summary: dict[str, Any], run_dir: Path) -> None:
    control = _load_runtime_control(run_dir)
    if not control:
        return

    metadata = summary.get("task_metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    runtime_hints = _runtime_hints_from_control(control)
    if runtime_hints:
        metadata["human_hints"] = _merge_hints(_human_hints_from_metadata(metadata), runtime_hints)

    runtime_history = _runtime_chat_history(control)
    if runtime_history:
        metadata["chat_history"] = runtime_history
        summary["chat_history"] = runtime_history

    summary["task_metadata"] = metadata


def _format_raw_responses(raw_responses: list[str]) -> str:
    if not raw_responses:
        return ""
    chunks = []
    for index, raw_response in enumerate(raw_responses, start=1):
        chunks.append(f"--- attempt {index} ---\n{raw_response}")
    return "\n\n".join(chunks) + "\n"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _page_screenshot_sha256(env: Any) -> str:
    try:
        if hasattr(env, "screenshot_bytes"):
            return hashlib.sha256(env.screenshot_bytes()).hexdigest()
        if getattr(env, "page", None) is None:
            return ""
        return hashlib.sha256(env.page.screenshot(full_page=False)).hexdigest()
    except Exception:
        return ""


def _candidate_text_signature(candidates: list[CandidateElement]) -> str:
    text = "\n".join(candidate.text for candidate in candidates if candidate.text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _page_text(env: Any, candidates: list[CandidateElement]) -> str:
    if hasattr(env, "page_text"):
        return env.page_text(candidates)
    candidate_text = "\n".join(candidate.text for candidate in candidates if candidate.text)
    if getattr(env, "page", None) is None:
        return candidate_text
    try:
        body_text = env.page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        body_text = ""
    return f"{body_text}\n{candidate_text}".strip()


def _current_url(env: Any, fallback: str) -> str:
    if hasattr(env, "current_url"):
        try:
            return env.current_url() or fallback
        except Exception:
            return fallback
    if getattr(env, "page", None) is None:
        return fallback
    return env.page.url or fallback
