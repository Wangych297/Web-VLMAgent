from __future__ import annotations

import argparse
import html
import json
import mimetypes
import re
import sys
import threading
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
TRACE_ROOT = PROJECT_ROOT / "traces"
WEBUI_TRACE_DIR = TRACE_ROOT / "webui_runs"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.base_client import load_env_file

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
REPORTS_LOCK = threading.Lock()
REPORTS_RUNNING: set[str] = set()
WEBUI_INITIAL_STEP_BUDGET = 50
WEBUI_STEP_CHUNK_SIZE = 15


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "WebMMAgentWorkbench/0.2"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        try:
            if route == "/api/runs":
                self._send_json({"runs": list_runs(), "jobs": list_jobs()})
                return
            if route == "/api/jobs":
                self._send_json({"jobs": list_jobs()})
                return
            if route == "/api/models":
                provider = query.get("provider", ["gemini"])[0]
                self._send_json(available_models(provider))
                return
            if route == "/api/debug-version":
                self._send_json({"version": "20260616-continue-route", "file": __file__})
                return
            if route == "/api/job":
                job_id = query.get("id", [""])[0]
                self._send_json({"job": _ensure_job_from_trace(job_id) if job_id else None})
                return
            if route == "/api/run":
                run_dir = _safe_path(query.get("path", [""])[0])
                self._send_json(load_run(run_dir))
                return
            if route == "/api/text":
                file_path = _safe_path(query.get("path", [""])[0])
                self._send_text(file_path.read_text(encoding="utf-8", errors="replace"))
                return
            if route == "/api/file":
                file_path = _safe_path(query.get("path", [""])[0])
                self._send_file(file_path)
                return
            if route == "/report":
                run_dir = _safe_path(query.get("path", [""])[0])
                self._send_html(render_report(run_dir))
                return

            if route == "/":
                self._send_file(STATIC_DIR / "index.html")
                return
            if route == "/demo":
                self._send_file(STATIC_DIR / "demo.html")
                return
            if route.startswith("/api/"):
                self._send_json({"error": f"api route not found: {route}"}, status=404)
                return
            static_path = _static_path(route)
            if static_path is not None:
                self._send_file(static_path)
                return

            self._send_json({"error": "not found"}, status=404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/")
        try:
            if route == "/api/tasks":
                payload = self._read_json()
                job = start_task_job(payload)
                self._send_json({"job": job}, status=201)
                return
            if route == "/api/tasks/approve":
                payload = self._read_json()
                job = approve_task_plan(payload)
                self._send_json({"job": job})
                return
            if route == "/api/tasks/continue":
                payload = self._read_json()
                job = continue_task(payload)
                self._send_json({"job": job})
                return
            if route == "/api/tasks/stop":
                payload = self._read_json()
                job = stop_task(payload)
                self._send_json({"job": job})
                return
            if route == "/api/reports/retry":
                payload = self._read_json()
                job = retry_report_generation(payload)
                self._send_json({"job": job})
                return
            self._send_json({"error": f"not found: {parsed.path}"}, status=404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValueError("request body is too large")
        body = self.rfile.read(length).decode("utf-8")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "file not found"}, status=404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_task_job(payload: dict[str, Any]) -> dict[str, Any]:
    instruction = _required_str(payload, "task")
    user_start_url = str(payload.get("url", "")).strip()
    chat_history = _normalize_chat_history(payload.get("chat_history"))
    if not chat_history:
        chat_history = [{"role": "user", "text": instruction}]

    provider = str(payload.get("provider", "gemini")).strip().lower()
    if provider not in {"openai", "deepseek", "gemini"}:
        raise ValueError("provider must be openai, deepseek or gemini")
    model = str(payload.get("model") or "").strip() or default_model_name(provider)

    browser_backend = str(payload.get("browser_backend", "playwright")).strip().lower()
    if browser_backend not in {"playwright", "web_access"}:
        raise ValueError("browser_backend must be playwright or web_access")

    max_steps = WEBUI_INITIAL_STEP_BUDGET

    headless = _as_bool(payload.get("headless", True))
    if user_start_url:
        parsed = urlparse(user_start_url)
    else:
        parsed = urlparse("https://planned.local/")
    if parsed.scheme not in {"http", "https", "file"}:
        raise ValueError("url must start with http://, https://, or file://")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    host_slug = _slug(parsed.hostname or "planned")
    job_id = f"{timestamp}_{host_slug}"
    run_dir = WEBUI_TRACE_DIR / job_id
    job = {
        "id": job_id,
        "status": "queued",
        "success": None,
        "url": user_start_url,
        "task": instruction,
        "provider": provider,
        "model": model,
        "browser_backend": browser_backend,
        "max_steps": max_steps,
        "headless": headless,
        "trace_path": _rel(run_dir),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": None,
        "ended_at": None,
        "summary": None,
        "error": "",
        "planner": None,
        "chat_history": chat_history,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job

    thread = threading.Thread(
        target=_run_task_job,
        args=(job_id, user_start_url, instruction, max_steps, provider, model, browser_backend, headless, chat_history),
        daemon=True,
    )
    thread.start()
    return dict(job)


def approve_task_plan(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _required_str(payload, "job_id")
    approved = _as_bool(payload.get("approved", False))
    reply = str(payload.get("reply", "")).strip()
    if not approved:
        raise ValueError("approved must be true to run the planned task")

    job = _ensure_job_from_trace(job_id)
    if job is None:
        raise ValueError(f"job not found: {job_id}")
    if job.get("status") in {"running", "done"}:
        raise ValueError(f"job is already {job.get('status')}")

    planner = job.get("planner")
    if not isinstance(planner, dict) or not planner:
        planner = _load_dict(WEBUI_TRACE_DIR / job_id / "task_plan.json")
    if str(planner.get("status") or "") != "run":
        raise ValueError("job does not have an executable planner result")
    start_url = str(planner.get("start_url") or "").strip()
    refined_instruction = str(planner.get("refined_instruction") or job.get("task") or "").strip()
    if urlparse(start_url).scheme not in {"http", "https", "file"}:
        raise ValueError("planner start_url is invalid")
    if not refined_instruction:
        raise ValueError("planner refined_instruction is empty")

    chat_history = _job_chat_history(job)
    approval_text = reply or "同意，开始执行。"
    chat_history = _append_chat_history(chat_history, "user", approval_text)

    approved_at = datetime.now().isoformat(timespec="seconds")
    _update_job(
        job_id,
        status="queued",
        success=None,
        url=start_url,
        task=refined_instruction,
        planner=planner,
        summary=None,
        error="",
        approved_at=approved_at,
        chat_history=chat_history,
    )
    thread = threading.Thread(
        target=_run_approved_plan_job,
        args=(job_id, planner, reply, approved_at, chat_history),
        daemon=True,
    )
    thread.start()
    return get_job(job_id) or job


def stop_task(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _required_str(payload, "job_id")
    reply = str(payload.get("reply", "")).strip() or "停止，不继续。"
    job = _ensure_job_from_trace(job_id)
    if job is None:
        raise ValueError(f"job not found: {job_id}")

    run_dir = WEBUI_TRACE_DIR / job_id
    config = _load_dict(run_dir / "run_config.json")
    summary = _load_dict(run_dir / "run_summary.json")
    metadata_payload = summary.get("task_metadata") or config.get("task_metadata")
    metadata = metadata_payload if isinstance(metadata_payload, dict) else {}

    chat_history = (
        _normalize_chat_history(summary.get("chat_history"))
        or _normalize_chat_history(metadata.get("chat_history"))
        or _job_chat_history(job)
    )
    chat_history = _append_chat_history(chat_history, "user", reply)
    stop_text = "好的，已停止，不再继续执行。"
    chat_history = _append_chat_history(chat_history, "agent", stop_text)

    ended_at = datetime.now().isoformat(timespec="seconds")
    metadata = {**metadata, "chat_history": chat_history, "stopped_by_user": True}
    _record_runtime_control(
        run_dir,
        chat_history=chat_history,
        stop_requested=True,
        stop_reason=stop_text,
    )
    summary = {
        **summary,
        "run_id": summary.get("run_id") or job_id,
        "success": False,
        "final_status": "user_stopped",
        "final_reason": stop_text,
        "final_answer": stop_text,
        "ended_at": ended_at,
        "task_metadata": metadata,
        "chat_history": chat_history,
    }
    config = {**config, "task_metadata": metadata, "chat_history": chat_history, "max_steps": WEBUI_STEP_CHUNK_SIZE}
    _write_json(run_dir / "run_config.json", config)
    _write_json(run_dir / "run_summary.json", summary)
    _update_job(
        job_id,
        status="done",
        success=False,
        summary=summary,
        ended_at=ended_at,
        chat_history=chat_history,
    )
    return get_job(job_id) or job


def continue_task(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _required_str(payload, "job_id")
    reply = str(payload.get("reply", "")).strip()
    live_job_before_rehydrate = get_job(job_id)
    job = _ensure_job_from_trace(job_id)
    if job is None:
        raise ValueError(f"job not found: {job_id}")

    run_dir = WEBUI_TRACE_DIR / job_id
    config = _load_dict(run_dir / "run_config.json")
    summary = _load_dict(run_dir / "run_summary.json")
    if summary:
        summary = _with_stale_status(summary, config, run_dir)
    planner = job.get("planner") if isinstance(job.get("planner"), dict) else {}
    if not planner:
        planner = _load_dict(run_dir / "task_plan.json")
    metadata_payload = summary.get("task_metadata") or config.get("task_metadata")
    metadata = metadata_payload if isinstance(metadata_payload, dict) else {}
    chat_history = (
        _normalize_chat_history(summary.get("chat_history"))
        or _normalize_chat_history(metadata.get("chat_history"))
        or _job_chat_history(job)
    )
    if reply:
        chat_history = _append_chat_history(chat_history, "user", reply)
    user_hint = _continue_hint_from_reply(reply)

    provider = str(config.get("provider") or job.get("provider") or "gemini")
    model = str(config.get("model") or summary.get("model") or job.get("model") or "").strip() or default_model_name(provider)
    browser_backend = str(config.get("browser_backend") or job.get("browser_backend") or "playwright")
    headless = _as_bool(config.get("headless", job.get("headless", True)))
    original_instruction = str(
        metadata.get("original_instruction")
        or planner.get("original_instruction")
        or config.get("instruction")
        or summary.get("instruction")
        or job.get("task")
        or ""
    ).strip()
    current_instruction = str(config.get("instruction") or summary.get("instruction") or job.get("task") or "").strip()
    if user_hint:
        current_instruction = f"{current_instruction or original_instruction}\n\n用户本轮提示：{user_hint}"
    if not current_instruction:
        raise ValueError("current task instruction is empty")

    status = str(summary.get("final_status") or job.get("status") or "")
    job_status = str(job.get("status") or "").lower()
    live_running = bool(
        live_job_before_rehydrate
        and live_job_before_rehydrate.get("status") in {"queued", "planning", "running"}
        and _run_has_active_work(summary, run_dir)
    )
    if live_running and (job_status in {"queued", "planning", "running"} or status in {"queued", "planning", "running"}):
        _record_runtime_message(
            job_id=job_id,
            reply=reply,
            chat_history=chat_history,
            metadata=metadata,
            config=config,
            summary=summary,
            user_hint=user_hint,
        )
        _update_job(
            job_id,
            status=job_status or status or "running",
            task=current_instruction,
            summary=summary,
            chat_history=chat_history,
            model=model,
        )
        return get_job(job_id) or job

    if status == "awaiting_approval":
        user_start_url = str(metadata.get("user_start_url") or planner.get("user_start_url") or "")
        _update_job(
            job_id,
            status="queued",
            success=None,
            task=current_instruction,
            summary=None,
            error="",
            chat_history=chat_history,
            model=model,
        )
        thread = threading.Thread(
            target=_run_task_job,
            args=(job_id, user_start_url, current_instruction, WEBUI_INITIAL_STEP_BUDGET, provider, model, browser_backend, headless, chat_history),
            daemon=True,
        )
        thread.start()
        return get_job(job_id) or job

    start_url = str(summary.get("final_url") or config.get("start_url") or job.get("url") or planner.get("start_url") or "").strip()
    if urlparse(start_url).scheme not in {"http", "https", "file"}:
        start_url = (
            _last_valid_completed_step_url(run_dir)
            or str(config.get("start_url") or job.get("url") or planner.get("start_url") or "").strip()
        )
    if urlparse(start_url).scheme not in {"http", "https", "file"}:
        raise ValueError("cannot continue because current URL is invalid")

    start_step_index = _count_steps(run_dir)
    continued_at = datetime.now().isoformat(timespec="seconds")
    task_metadata = {
        **metadata,
        "source": "webui",
        "job_id": job_id,
        "planner": planner or metadata.get("planner"),
        "original_instruction": original_instruction or current_instruction,
        "user_start_url": str(metadata.get("user_start_url") or ""),
        "chat_history": chat_history,
        "human_hints": _append_human_hint(metadata.get("human_hints"), user_hint),
        "continuation": {
            "reply": reply,
            "user_hint": user_hint,
            "continued_at": continued_at,
            "start_step_index": start_step_index,
            "step_budget": WEBUI_STEP_CHUNK_SIZE,
        },
    }
    _update_job(
        job_id,
        status="running",
        success=None,
        url=start_url,
        task=current_instruction,
        summary=None,
        error="",
        chat_history=chat_history,
        model=model,
    )
    thread = threading.Thread(
        target=_run_continue_task_job,
        args=(job_id, start_url, current_instruction, provider, model, browser_backend, headless, chat_history, task_metadata, start_step_index),
        daemon=True,
    )
    thread.start()
    return get_job(job_id) or job


def retry_report_generation(payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _required_str(payload, "job_id")
    job = _ensure_job_from_trace(job_id)
    if job is None:
        raise ValueError(f"job not found: {job_id}")

    run_dir = WEBUI_TRACE_DIR / job_id
    summary = _load_dict(run_dir / "run_summary.json")
    config = _load_dict(run_dir / "run_config.json")
    final_answer = str(summary.get("final_answer") or "").strip()
    if not final_answer:
        raise ValueError("cannot generate report because final_answer is empty")

    with REPORTS_LOCK:
        if job_id in REPORTS_RUNNING:
            return _ensure_job_from_trace(job_id) or job
        REPORTS_RUNNING.add(job_id)

    summary["report_status"] = "queued"
    summary["report_started_at"] = datetime.now().isoformat(timespec="seconds")
    summary["report_ended_at"] = ""
    summary["final_report_error"] = ""
    _write_json(run_dir / "run_summary.json", summary)

    _update_job(
        job_id,
        status="running",
        success=None,
        summary=summary,
        error="",
        model=str(summary.get("model") or config.get("model") or job.get("model") or ""),
    )
    thread = threading.Thread(target=_run_report_generation_job, args=(job_id,), daemon=True)
    thread.start()
    return get_job(job_id) or job


def _run_report_generation_job(job_id: str) -> None:
    run_dir = WEBUI_TRACE_DIR / job_id
    try:
        from eval.runner import _write_final_html_report, create_model_client, write_json
        from models.base_client import load_env_file

        summary = _load_dict(run_dir / "run_summary.json")
        config = _load_dict(run_dir / "run_config.json")
        instruction = str(summary.get("instruction") or config.get("instruction") or "").strip()
        provider = str(summary.get("provider") or config.get("provider") or "gemini").strip() or "gemini"
        model = str(summary.get("model") or config.get("model") or "").strip() or default_model_name(provider)

        summary["report_status"] = "running"
        summary["report_started_at"] = summary.get("report_started_at") or datetime.now().isoformat(timespec="seconds")
        summary["report_ended_at"] = ""
        summary["final_report_path"] = ""
        summary["final_report_error"] = ""
        if summary.get("blocked_status") == "final_report_generation_failed":
            summary["success"] = True
            summary["final_status"] = "success"
            summary["final_reason"] = summary.get("pre_report_final_reason") or "answer text is present in current page evidence"
            summary["blocked_status"] = ""
        write_json(run_dir / "run_summary.json", summary)

        load_env_file(PROJECT_ROOT / ".env")
        client = create_model_client(provider, model=model)
        _write_final_html_report(
            client=client,
            run_dir=run_dir,
            instruction=instruction,
            summary=summary,
        )

        summary["report_status"] = "success" if summary.get("final_report_path") else "fail"
        summary["report_ended_at"] = datetime.now().isoformat(timespec="seconds")
        write_json(run_dir / "run_summary.json", summary)
        _update_job(
            job_id,
            status="done",
            success=bool(summary.get("success")),
            summary=summary,
            ended_at=summary["report_ended_at"],
            error=str(summary.get("final_report_error") or ""),
            model=model,
        )
    except Exception as exc:
        summary = _load_dict(run_dir / "run_summary.json")
        summary["success"] = False
        summary["final_status"] = "fail"
        summary["blocked_status"] = "final_report_generation_failed"
        summary["final_reason"] = "final_report_generation_failed"
        summary["report_status"] = "fail"
        summary["report_ended_at"] = datetime.now().isoformat(timespec="seconds")
        summary["final_report_path"] = ""
        summary["final_report_error"] = f"{exc.__class__.__name__}: {exc}"
        _write_json(run_dir / "run_summary.json", summary)
        _update_job(
            job_id,
            status="done",
            success=False,
            summary=summary,
            ended_at=summary["report_ended_at"],
            error=str(summary.get("final_report_error") or ""),
        )
    finally:
        with REPORTS_LOCK:
            REPORTS_RUNNING.discard(job_id)


def _record_runtime_message(
    job_id: str,
    reply: str,
    chat_history: list[dict[str, str]],
    metadata: dict[str, Any],
    config: dict[str, Any],
    summary: dict[str, Any],
    user_hint: str,
) -> None:
    run_dir = WEBUI_TRACE_DIR / job_id
    hints = _append_human_hint(metadata.get("human_hints"), user_hint)
    metadata = {**metadata, "chat_history": chat_history, "human_hints": hints}
    summary.update(
        {
            "task_metadata": metadata,
            "chat_history": chat_history,
            "final_reason": summary.get("final_reason") or "任务运行中，已收到用户消息。",
        }
    )
    config.update({"task_metadata": metadata, "chat_history": chat_history})
    _record_runtime_control(
        run_dir,
        chat_history=chat_history,
        human_hints=hints,
        last_user_message=reply,
    )
    _write_json(run_dir / "run_config.json", config)
    _write_json(run_dir / "run_summary.json", summary)


def _record_runtime_control(
    run_dir: Path,
    chat_history: list[dict[str, str]] | None = None,
    human_hints: list[str] | None = None,
    last_user_message: str = "",
    stop_requested: bool | None = None,
    stop_reason: str = "",
) -> None:
    control = _load_dict(run_dir / "runtime_control.json")
    if chat_history is not None:
        control["chat_history"] = chat_history
    if human_hints is not None:
        control["human_hints"] = human_hints[-5:]
    if last_user_message:
        control["last_user_message"] = last_user_message
    if stop_requested is not None:
        control["stop_requested"] = stop_requested
    if stop_reason:
        control["stop_reason"] = stop_reason
    control["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _write_json(run_dir / "runtime_control.json", control)


def _run_task_job(
    job_id: str,
    user_start_url: str,
    instruction: str,
    max_steps: int,
    provider: str,
    model: str,
    browser_backend: str,
    headless: bool,
    chat_history: list[dict[str, str]],
) -> None:
    started_at = datetime.now().isoformat(timespec="seconds")
    base_metadata = {
        "source": "webui",
        "job_id": job_id,
        "original_instruction": instruction,
        "user_start_url": user_start_url,
        "chat_history": chat_history,
        "model": model,
    }
    _update_job(job_id, status="planning", started_at=started_at)
    _write_interim_run(
        job_id=job_id,
        start_url=user_start_url,
        instruction=instruction,
        max_steps=max_steps,
        provider=provider,
        model=model,
        browser_backend=browser_backend,
        headless=headless,
        final_status="planning",
        final_reason="planner is choosing the start website and execution goal",
        started_at=started_at,
        task_metadata=base_metadata,
        success=None,
    )
    try:
        from agents.task_planner import TaskPlanner
        from eval.runner import create_model_client
        from models.base_client import load_env_file

        load_env_file(PROJECT_ROOT / ".env")
        planner = TaskPlanner(create_model_client(provider, model=model))
        plan = planner.plan(instruction=instruction, user_start_url=user_start_url)
        plan_data = {
            "status": plan.status,
            "start_url": plan.start_url,
            "refined_instruction": plan.refined_instruction,
            "question": plan.question,
            "reason": plan.reason,
            "risk_notes": plan.risk_notes,
            "original_instruction": instruction,
            "user_start_url": user_start_url,
            "model": model,
            "raw_response": plan.raw_response,
        }
        run_dir = WEBUI_TRACE_DIR / job_id
        _write_json(run_dir / "task_plan.json", plan_data)
        _write_text(run_dir / "planner_raw_response.txt", plan.raw_response)
        _update_job(job_id, planner=plan_data)

        if plan.status == "ask_user":
            waiting_history = _append_chat_history(chat_history, "agent", plan.question)
            summary = _write_planner_waiting_run(
                job_id=job_id,
                instruction=instruction,
                max_steps=max_steps,
                provider=provider,
                model=model,
                browser_backend=browser_backend,
                headless=headless,
                question=plan.question,
                planner=plan_data,
                task_metadata={**base_metadata, "planner": plan_data, "chat_history": waiting_history},
            )
            _update_job(
                job_id,
                status="waiting",
                success=False,
                url="",
                task=instruction,
                summary=summary,
                chat_history=waiting_history,
                ended_at=datetime.now().isoformat(timespec="seconds"),
            )
            return

        task_metadata = {
            **base_metadata,
            "planner": plan_data,
        }
        summary = _write_approval_run(
            job_id=job_id,
            original_instruction=instruction,
            max_steps=max_steps,
            provider=provider,
            model=model,
            browser_backend=browser_backend,
            headless=headless,
            started_at=started_at,
            task_metadata=task_metadata,
            planner=plan_data,
        )
        _update_job(
            job_id,
            status="awaiting_approval",
            success=None,
            url=plan.start_url,
            task=plan.refined_instruction,
            summary=summary,
            chat_history=summary.get("chat_history", chat_history),
        )
    except Exception as exc:
        error_text = f"{exc.__class__.__name__}: {exc}"
        error_traceback = traceback.format_exc()
        try:
            _write_error_run(
                job_id=job_id,
                start_url=user_start_url,
                instruction=instruction,
                max_steps=max_steps,
                provider=provider,
                model=model,
                browser_backend=browser_backend,
                headless=headless,
                error_text=error_text,
                error_traceback=error_traceback,
                started_at=started_at,
                task_metadata={**base_metadata, "chat_history": _append_chat_history(chat_history, "agent", error_text)},
            )
        except Exception:
            pass
        _update_job(
            job_id,
            status="error",
            success=False,
            error=error_text,
            traceback=error_traceback,
            ended_at=datetime.now().isoformat(timespec="seconds"),
        )


def _run_approved_plan_job(
    job_id: str,
    planner: dict[str, Any],
    approval_reply: str,
    approved_at: str,
    chat_history: list[dict[str, str]],
) -> None:
    job = _ensure_job_from_trace(job_id)
    if job is None:
        return
    chat_history = _normalize_chat_history(chat_history) or _job_chat_history(job)

    start_url = str(planner.get("start_url") or "").strip()
    refined_instruction = str(planner.get("refined_instruction") or job.get("task") or "").strip()
    provider = str(job.get("provider") or "gemini")
    model = str(job.get("model") or "").strip() or default_model_name(provider)
    browser_backend = str(job.get("browser_backend") or "playwright")
    max_steps = WEBUI_INITIAL_STEP_BUDGET
    headless = _as_bool(job.get("headless", True))
    started_at = str(job.get("started_at") or datetime.now().isoformat(timespec="seconds"))
    original_instruction = str(
        (planner.get("original_instruction") or "")
        or job.get("original_instruction")
        or job.get("task")
        or refined_instruction
    )

    task_metadata = {
        "source": "webui",
        "job_id": job_id,
        "planner": planner,
        "model": model,
        "original_instruction": original_instruction,
        "user_start_url": str(job.get("user_start_url") or ""),
        "chat_history": chat_history,
        "approval": {
            "reply": approval_reply,
            "approved_at": approved_at,
        },
    }

    _update_job(job_id, status="running", url=start_url, task=refined_instruction, model=model)
    _write_interim_run(
        job_id=job_id,
        start_url=start_url,
        instruction=refined_instruction,
        max_steps=max_steps,
        provider=provider,
        model=model,
        browser_backend=browser_backend,
        headless=headless,
        final_status="running",
        final_reason="user approved the planned website and execution goal",
        started_at=started_at,
        task_metadata=task_metadata,
        planner=planner,
        success=None,
    )
    try:
        from eval.runner import run_task

        summary = run_task(
            start_url=start_url,
            instruction=refined_instruction,
            max_steps=max_steps,
            provider=provider,
            model=model,
            browser_backend=browser_backend,
            trace_dir=WEBUI_TRACE_DIR,
            headless=headless,
            run_id=job_id,
            task_metadata=task_metadata,
            ask_user_on_failure=True,
        )
        final_history = _append_chat_history(chat_history, "agent", _final_chat_text(summary))
        summary["chat_history"] = final_history
        metadata = summary.get("task_metadata")
        if isinstance(metadata, dict):
            metadata["chat_history"] = final_history
            summary["task_metadata"] = metadata
        _write_json(WEBUI_TRACE_DIR / job_id / "run_summary.json", summary)
        job_status = "waiting" if summary.get("final_status") == "needs_user_input" else "done"
        _update_job(
            job_id,
            status=job_status,
            success=bool(summary.get("success")),
            summary=summary,
            chat_history=final_history,
            ended_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        error_text = f"{exc.__class__.__name__}: {exc}"
        error_traceback = traceback.format_exc()
        _write_error_run(
            job_id=job_id,
            start_url=start_url,
            instruction=refined_instruction,
            max_steps=max_steps,
            provider=provider,
            model=model,
            browser_backend=browser_backend,
            headless=headless,
            error_text=error_text,
            error_traceback=error_traceback,
            started_at=started_at,
            task_metadata={**task_metadata, "chat_history": _append_chat_history(chat_history, "agent", error_text)},
        )
        _update_job(
            job_id,
            status="error",
            success=False,
            error=error_text,
            traceback=error_traceback,
            ended_at=datetime.now().isoformat(timespec="seconds"),
        )


def _run_continue_task_job(
    job_id: str,
    start_url: str,
    instruction: str,
    provider: str,
    model: str,
    browser_backend: str,
    headless: bool,
    chat_history: list[dict[str, str]],
    task_metadata: dict[str, Any],
    start_step_index: int,
) -> None:
    started_at = datetime.now().isoformat(timespec="seconds")
    run_dir = WEBUI_TRACE_DIR / job_id
    previous_summary = _load_dict(run_dir / "run_summary.json")
    previous_started_at = str(previous_summary.get("started_at") or started_at)
    previous_duration = float(previous_summary.get("duration_seconds") or 0.0)
    planner = task_metadata.get("planner") if isinstance(task_metadata.get("planner"), dict) else None
    interim_summary = _write_interim_run(
        job_id=job_id,
        start_url=start_url,
        instruction=instruction,
        max_steps=start_step_index + WEBUI_STEP_CHUNK_SIZE,
        provider=provider,
        model=model,
        browser_backend=browser_backend,
        headless=headless,
        final_status="running",
        final_reason=f"continuing current task for another {WEBUI_STEP_CHUNK_SIZE} steps",
        started_at=previous_started_at,
        task_metadata=task_metadata,
        planner=planner,
        success=None,
    )
    interim_summary["duration_seconds"] = previous_duration
    _write_json(run_dir / "run_summary.json", interim_summary)
    try:
        from eval.runner import run_task

        summary = run_task(
            start_url=start_url,
            instruction=instruction,
            max_steps=WEBUI_STEP_CHUNK_SIZE,
            provider=provider,
            model=model,
            browser_backend=browser_backend,
            trace_dir=WEBUI_TRACE_DIR,
            headless=headless,
            run_id=job_id,
            task_metadata=task_metadata,
            ask_user_on_failure=True,
            start_step_index=start_step_index,
        )
        final_history = _append_chat_history(chat_history, "agent", _final_chat_text(summary))
        summary["chat_history"] = final_history
        metadata = summary.get("task_metadata")
        if isinstance(metadata, dict):
            metadata["chat_history"] = final_history
            summary["task_metadata"] = metadata
        _write_json(WEBUI_TRACE_DIR / job_id / "run_summary.json", summary)
        job_status = "waiting" if summary.get("final_status") == "needs_user_input" else "done"
        _update_job(
            job_id,
            status=job_status,
            success=bool(summary.get("success")),
            summary=summary,
            chat_history=final_history,
            ended_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        error_text = f"{exc.__class__.__name__}: {exc}"
        error_traceback = traceback.format_exc()
        _write_error_run(
            job_id=job_id,
            start_url=start_url,
            instruction=instruction,
            max_steps=start_step_index + WEBUI_STEP_CHUNK_SIZE,
            provider=provider,
            model=model,
            browser_backend=browser_backend,
            headless=headless,
            error_text=error_text,
            error_traceback=error_traceback,
            started_at=started_at,
            task_metadata={**task_metadata, "chat_history": _append_chat_history(chat_history, "agent", error_text)},
        )
        _update_job(
            job_id,
            status="error",
            success=False,
            error=error_text,
            traceback=error_traceback,
            ended_at=datetime.now().isoformat(timespec="seconds"),
        )


def _update_job(job_id: str, **fields: Any) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(fields)


def _write_interim_run(
    job_id: str,
    start_url: str,
    instruction: str,
    max_steps: int,
    provider: str,
    model: str,
    browser_backend: str,
    headless: bool,
    final_status: str,
    final_reason: str,
    started_at: str,
    final_answer: str = "",
    ended_at: str | None = None,
    task_metadata: dict[str, Any] | None = None,
    planner: dict[str, Any] | None = None,
    success: bool | None = False,
) -> dict[str, Any]:
    run_dir = WEBUI_TRACE_DIR / job_id
    metadata = task_metadata or {
        "source": "webui",
        "job_id": job_id,
        "original_instruction": instruction,
        "user_start_url": start_url,
    }
    if planner and "planner" not in metadata:
        metadata = {**metadata, "planner": planner}
    chat_history = _normalize_chat_history(metadata.get("chat_history"))
    if chat_history:
        metadata = {**metadata, "chat_history": chat_history}
    config = {
        "run_id": job_id,
        "start_url": start_url,
        "instruction": instruction,
        "max_steps": max_steps,
        "provider": provider,
        "model": model,
        "browser_backend": browser_backend,
        "web_access_browser": None,
        "headless": headless,
        "task_metadata": metadata,
        "chat_history": chat_history,
        "planner": planner,
        "started_at": started_at,
    }
    summary = {
        "run_id": job_id,
        "success": success,
        "final_status": final_status,
        "steps_used": _count_steps(run_dir),
        "trace_dir": _rel(run_dir),
        "instruction": instruction,
        "final_reason": final_reason,
        "final_answer": final_answer,
        "final_url": start_url,
        "errors": [],
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": 0.0,
        "task_metadata": metadata,
        "chat_history": chat_history,
        "provider": provider,
        "model": model,
        "browser_backend": browser_backend,
    }
    _write_json(run_dir / "run_config.json", config)
    _write_json(run_dir / "run_summary.json", summary)
    return summary


def _write_approval_run(
    job_id: str,
    original_instruction: str,
    max_steps: int,
    provider: str,
    model: str,
    browser_backend: str,
    headless: bool,
    started_at: str,
    task_metadata: dict[str, Any],
    planner: dict[str, Any],
) -> dict[str, Any]:
    confirmation = _plan_confirmation_text(planner, original_instruction)
    metadata = {
        **task_metadata,
        "chat_history": _append_chat_history(task_metadata.get("chat_history"), "agent", confirmation),
    }
    return _write_interim_run(
        job_id=job_id,
        start_url=str(planner.get("start_url") or ""),
        instruction=original_instruction,
        max_steps=max_steps,
        provider=provider,
        model=model,
        browser_backend=browser_backend,
        headless=headless,
        final_status="awaiting_approval",
        final_reason=confirmation,
        final_answer=confirmation,
        started_at=started_at,
        task_metadata=metadata,
        planner=planner,
        success=None,
    )


def _write_planner_waiting_run(
    job_id: str,
    instruction: str,
    max_steps: int,
    provider: str,
    model: str,
    browser_backend: str,
    headless: bool,
    question: str,
    planner: dict[str, Any],
    task_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = str((get_job(job_id) or {}).get("started_at") or datetime.now().isoformat(timespec="seconds"))
    return _write_interim_run(
        job_id=job_id,
        start_url="",
        instruction=instruction,
        max_steps=max_steps,
        provider=provider,
        model=model,
        browser_backend=browser_backend,
        headless=headless,
        final_status="needs_user_input",
        final_reason=question,
        final_answer=question,
        started_at=started_at,
        ended_at=datetime.now().isoformat(timespec="seconds"),
        task_metadata=task_metadata,
        planner=planner,
    )


def _write_error_run(
    job_id: str,
    start_url: str,
    instruction: str,
    max_steps: int,
    provider: str,
    model: str,
    browser_backend: str,
    headless: bool,
    error_text: str,
    error_traceback: str,
    started_at: str,
    task_metadata: dict[str, Any] | None = None,
) -> None:
    summary = _write_interim_run(
        job_id=job_id,
        start_url=start_url,
        instruction=instruction,
        max_steps=max_steps,
        provider=provider,
        model=model,
        browser_backend=browser_backend,
        headless=headless,
        final_status="fatal_error",
        final_reason=error_text,
        started_at=started_at,
        ended_at=datetime.now().isoformat(timespec="seconds"),
        task_metadata=task_metadata,
    )
    summary["errors"] = [
        {
            "error": error_text,
            "traceback": error_traceback,
        }
    ]
    _write_json(WEBUI_TRACE_DIR / job_id / "run_summary.json", summary)


def _ensure_job_from_trace(job_id: str) -> dict[str, Any] | None:
    existing = get_job(job_id)
    run_dir = WEBUI_TRACE_DIR / job_id
    if not run_dir.exists() and existing is None:
        return None

    config = _load_dict(run_dir / "run_config.json")
    summary = _load_dict(run_dir / "run_summary.json")
    if summary:
        summary = _with_stale_status(summary, config, run_dir)
    planner = _load_dict(run_dir / "task_plan.json")
    if not planner:
        planner_payload = config.get("planner")
        planner = planner_payload if isinstance(planner_payload, dict) else {}
    metadata = config.get("task_metadata")
    task_metadata = metadata if isinstance(metadata, dict) else {}
    chat_history = (
        _normalize_chat_history(summary.get("chat_history"))
        or _normalize_chat_history(task_metadata.get("chat_history"))
        or _normalize_chat_history(config.get("chat_history"))
    )

    status = str(summary.get("final_status") or (existing.get("status") if existing else ""))
    job_status = _job_status_from_final_status(status)
    job = {
        "id": job_id,
        "status": job_status,
        "success": summary.get("success"),
        "url": summary.get("final_url") or config.get("start_url") or "",
        "task": config.get("instruction") or summary.get("instruction") or "",
        "provider": config.get("provider") or "gemini",
        "model": config.get("model") or summary.get("model") or "",
        "browser_backend": config.get("browser_backend") or "playwright",
        "max_steps": config.get("max_steps") or WEBUI_STEP_CHUNK_SIZE,
        "headless": config.get("headless", True),
        "trace_path": _rel(run_dir),
        "created_at": summary.get("started_at") or config.get("started_at") or datetime.now().isoformat(timespec="seconds"),
        "started_at": summary.get("started_at") or config.get("started_at"),
        "ended_at": summary.get("ended_at"),
        "summary": summary or None,
        "error": "",
        "planner": planner,
        "task_metadata": task_metadata,
        "chat_history": chat_history,
        "original_instruction": task_metadata.get("original_instruction", ""),
        "user_start_url": task_metadata.get("user_start_url", ""),
    }
    if existing:
        job = {**existing, **job, "planner": existing.get("planner") or planner}
    with JOBS_LOCK:
        JOBS[job_id] = job
    return dict(job)


def _job_status_from_final_status(final_status: str) -> str:
    normalized = final_status.strip().lower()
    if normalized in {"planning", "running", "awaiting_approval"}:
        return normalized
    if normalized == "needs_user_input":
        return "waiting"
    if normalized in {"success", "fail", "max_steps_exceeded", "stuck", "step_error", "fatal_error", "model_error", "user_stopped", "interrupted"}:
        return "done" if normalized == "success" else "error" if normalized.endswith("error") else "done"
    return normalized or "queued"


def _with_stale_status(summary: dict[str, Any], config: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    if not summary:
        return summary
    summary = dict(summary)
    complete_steps = _count_steps(run_dir)
    try:
        recorded_steps = int(summary.get("steps_used") or 0)
    except (TypeError, ValueError):
        recorded_steps = 0
    if complete_steps > recorded_steps:
        summary["steps_used"] = complete_steps

    status = str(summary.get("final_status") or "")
    last_url = _last_completed_step_url(run_dir)
    if last_url and urlparse(last_url).scheme not in {"http", "https", "file"}:
        last_url = _last_valid_completed_step_url(run_dir) or last_url
    if last_url and (not summary.get("final_url") or status in {"planning", "running"}):
        summary["final_url"] = last_url

    if status not in {"planning", "running"}:
        return summary
    run_id = str(summary.get("run_id") or config.get("run_id") or run_dir.name)
    live_job = get_job(run_id)
    if live_job and live_job.get("status") in {"queued", "planning", "running"}:
        last_activity = _last_trace_activity_time(summary, run_dir)
        if last_activity is None or datetime.now() - last_activity < timedelta(minutes=15):
            return summary
    started_at = str(summary.get("started_at") or config.get("started_at") or "")
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        started = datetime.min
    if started != datetime.min and datetime.now() - started < timedelta(seconds=45):
        return summary

    stale = dict(summary)
    stale["success"] = False
    stale["final_status"] = "interrupted"
    stale["final_reason"] = stale.get("final_reason") or "这个历史运行在服务器重启或中断前没有正常结束。"
    return stale


def _last_completed_step_url(run_dir: Path) -> str:
    for step_dir in sorted((path for path in run_dir.glob("step_*") if path.is_dir()), reverse=True):
        execution = _load_dict(step_dir / "execution_result.json")
        url = str(execution.get("url_after") or execution.get("url_before") or "").strip()
        if url:
            return url
    return ""


def _last_valid_completed_step_url(run_dir: Path) -> str:
    for step_dir in sorted((path for path in run_dir.glob("step_*") if path.is_dir()), reverse=True):
        execution = _load_dict(step_dir / "execution_result.json")
        for key in ("url_after", "url_before"):
            url = str(execution.get(key) or "").strip()
            if urlparse(url).scheme in {"http", "https", "file"}:
                return url
    return ""


def _run_has_active_work(summary: dict[str, Any], run_dir: Path) -> bool:
    status = str(summary.get("final_status") or "").strip().lower()
    if status not in {"queued", "planning", "running"}:
        return False

    active_step = str(summary.get("active_step") or "").strip()
    if active_step:
        step_dir = run_dir / active_step
        return step_dir.exists() and not (step_dir / "execution_result.json").exists()

    for step_dir in sorted((path for path in run_dir.glob("step_*") if path.is_dir()), reverse=True):
        if not (step_dir / "execution_result.json").exists() and any(step_dir.iterdir()):
            last_activity = _last_trace_activity_time({}, step_dir)
            return last_activity is not None and datetime.now() - last_activity < timedelta(minutes=15)
    return False


def _last_trace_activity_time(summary: dict[str, Any], run_dir: Path) -> datetime | None:
    updated_at = str(summary.get("updated_at") or "").strip()
    if updated_at:
        try:
            return datetime.fromisoformat(updated_at)
        except ValueError:
            pass
    candidates = [run_dir / "run_summary.json"]
    candidates.extend(path for path in run_dir.glob("step_*/*") if path.is_file())
    mtimes = [path.stat().st_mtime for path in candidates if path.exists()]
    if not mtimes:
        return None
    return datetime.fromtimestamp(max(mtimes))


def _plan_confirmation_text(planner: dict[str, Any], original_instruction: str) -> str:
    start_url = str(planner.get("start_url") or "").strip()
    refined_instruction = str(planner.get("refined_instruction") or "").strip()
    reason = str(planner.get("reason") or "").strip()
    risk_notes = str(planner.get("risk_notes") or "").strip()
    if _contains_cjk(original_instruction):
        return "\n".join(
            part
            for part in [
                "我先确认一下执行计划，得到你的同意后才会打开网站继续：",
                f"要打开的网站：{start_url or '未确定'}",
                f"准备怎么做：{refined_instruction or original_instruction}",
                f"选择理由：{reason or '模型认为这是合适的公开来源。'}",
                f"风险/限制：{risk_notes}" if risk_notes else "",
                "如果同意，请点“同意，开始执行”；如果不同意，直接在上面的聊天框里告诉我怎么改。",
            ]
            if part
        )
    return "\n".join(
        part
        for part in [
            "Please approve this plan before I open the website:",
            f"Website: {start_url or 'not selected'}",
            f"How I will proceed: {refined_instruction or original_instruction}",
            f"Reason: {reason or 'The planner selected it as a suitable public source.'}",
            f"Risk notes: {risk_notes}" if risk_notes else "",
            'Click "Approve and run" to continue, or reply with changes.',
        ]
        if part
    )


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def list_jobs() -> list[dict[str, Any]]:
    with JOBS_LOCK:
        jobs = [dict(job) for job in JOBS.values()]
    return sorted(jobs, key=lambda item: str(item.get("created_at", "")), reverse=True)


def available_models(provider: str) -> dict[str, Any]:
    normalized = _normalize_provider(provider)
    default_model = default_model_name(normalized)
    api_models = _fetch_api_model_ids(normalized)
    fallback_models = _fallback_model_ids(normalized)
    seen: set[str] = set()
    model_items: list[dict[str, Any]] = []

    for model_id in [*api_models, default_model, *fallback_models]:
        model_id = str(model_id or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        model_items.append(
            {
                "id": model_id,
                "label": model_id,
                "source": "api" if model_id in api_models else "fallback",
                "default": model_id == default_model,
            }
        )

    return {
        "provider": normalized,
        "default_model": default_model,
        "models": model_items,
        "api_models_available": bool(api_models),
    }


def default_model_name(provider: str) -> str:
    normalized = _normalize_provider(provider)
    load_env_file(PROJECT_ROOT / ".env")
    if normalized == "deepseek":
        from models.base_client import env_or_default

        return env_or_default("DEEPSEEK_MODEL", "deepseek-chat")
    if normalized == "openai":
        from models.base_client import env_or_default

        return env_or_default("OPENAI_MODEL", "gpt-4o-mini")

    from models.base_client import env_or_default

    return env_or_default("GEMINI_MODEL", "gemini-3.5-flash")


def _fetch_api_model_ids(provider: str) -> list[str]:
    load_env_file(PROJECT_ROOT / ".env")
    try:
        from models.base_client import env_or_default

        normalized = _normalize_provider(provider)
        if normalized == "deepseek":
            api_key = env_or_default("DEEPSEEK_API_KEY", "")
            base_url = env_or_default("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        else:
            api_key = env_or_default("OPENAI_API_KEY", "")
            base_url = env_or_default("OPENAI_BASE_URL", "https://api.openai.com/v1")
        if not api_key:
            return []
        request = urllib.request.Request(
            url=f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return []

    raw_models = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        return []
    model_ids: list[str] = []
    for item in raw_models:
        if isinstance(item, str):
            model_id = item
        elif isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or "").strip()
        else:
            model_id = ""
        if model_id:
            model_ids.append(model_id)
    return sorted(set(model_ids), key=lambda value: value.lower())


def _fallback_model_ids(provider: str) -> list[str]:
    normalized = _normalize_provider(provider)
    if normalized == "deepseek":
        return ["deepseek-chat", "deepseek-reasoner"]
    if normalized == "openai":
        return ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o4-mini"]
    return [
        "gemini-3.5-flash",
        "gemini-3.5-flash-nothinking",
        "gemini-3.1-flash-lite",
        "gemini-3.1-lite",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
    ]


def _normalize_provider(provider: str) -> str:
    normalized = str(provider or "gemini").strip().lower()
    if normalized not in {"openai", "deepseek", "gemini"}:
        return "gemini"
    return normalized


def get_job(job_id: str) -> dict[str, Any] | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def list_runs() -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    if not TRACE_ROOT.exists():
        return runs

    run_dirs = {path.parent for path in TRACE_ROOT.glob("*/*/run_config.json")}
    run_dirs.update(path.parent for path in TRACE_ROOT.glob("*/*/run_summary.json"))

    for run_dir in run_dirs:
        summary = _load_dict(run_dir / "run_summary.json")
        config = _load_dict(run_dir / "run_config.json")
        summary = _with_stale_status(summary, config, run_dir)
        success = summary.get("success")
        runs.append(
            {
                "path": _rel(run_dir),
                "group": run_dir.parent.name,
                "name": run_dir.name,
                "success": success if isinstance(success, bool) else None,
                "status": summary.get("final_status", "running" if config else "unknown"),
                "steps": summary.get("steps_used", _count_steps(run_dir)),
                "duration": summary.get("duration_seconds", 0),
                "instruction": summary.get("instruction") or config.get("instruction", ""),
                "start_url": config.get("start_url", ""),
                "provider": config.get("provider", ""),
                "model": config.get("model") or summary.get("model") or "",
                "browser_backend": config.get("browser_backend", "playwright"),
                "started_at": summary.get("started_at") or config.get("started_at", ""),
            }
        )
    return sorted(runs, key=lambda item: str(item["started_at"]), reverse=True)


def load_run(run_dir: Path) -> dict[str, object]:
    summary = _load_dict(run_dir / "run_summary.json")
    config = _load_dict(run_dir / "run_config.json")
    summary = _with_stale_status(summary, config, run_dir)
    step_dirs = sorted(path for path in run_dir.glob("step_*") if path.is_dir())
    steps = []
    for step_dir in step_dirs:
        parsed_action = _load_dict(step_dir / "parsed_action.json")
        execution = _load_dict(step_dir / "execution_result.json")
        progress = _load_dict(step_dir / "progress_check.json")
        verification = _load_dict(step_dir / "final_verification.json")
        steps.append(
            {
                "name": step_dir.name,
                "path": _rel(step_dir),
                "action": parsed_action,
                "execution_ok": execution.get("ok") if "ok" in execution else None,
                "progress": progress.get("reason", ""),
                "verification": verification.get("reason", ""),
                "screenshot": _rel_if_exists(step_dir / "screenshot.png"),
                "marked_screenshot": _rel_if_exists(step_dir / "marked_screenshot.png"),
                "files": {
                    "candidates": _rel_if_exists(step_dir / "candidates.json"),
                    "accessibility_tree": _rel_if_exists(step_dir / "accessibility_tree.txt"),
                    "page_text": _rel_if_exists(step_dir / "page_text.txt"),
                    "prompt": _rel_if_exists(step_dir / "prompt.txt"),
                    "raw_response": _rel_if_exists(step_dir / "raw_response.txt"),
                    "execution": _rel_if_exists(step_dir / "execution_result.json"),
                    "progress": _rel_if_exists(step_dir / "progress_check.json"),
                    "final_verification": _rel_if_exists(step_dir / "final_verification.json"),
                },
            }
        )
    if config and not summary:
        summary = {
            "run_id": config.get("run_id", run_dir.name),
            "success": False,
            "final_status": "running",
            "steps_used": len(steps),
            "trace_dir": _rel(run_dir),
            "started_at": config.get("started_at", ""),
            "duration_seconds": 0,
            "final_reason": "run is still writing trace files",
            "final_answer": "",
        }
    generated_report = run_dir / "final_report.html"
    has_final_report = generated_report.exists() and generated_report.is_file() and generated_report.stat().st_size > 0
    return {
        "path": _rel(run_dir),
        "summary": summary,
        "config": config,
        "steps": steps,
        "report_url": f"/report?path={_rel(run_dir)}",
        "has_final_report": has_final_report,
        "final_report_path": _rel(generated_report) if has_final_report else "",
    }


def render_report(run_dir: Path) -> str:
    if not run_dir.exists() or not run_dir.is_dir():
        raise ValueError("run directory not found")

    generated_report = run_dir / "final_report.html"
    if generated_report.exists() and generated_report.is_file():
        text = generated_report.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return _rewrite_generated_report_asset_paths(text, run_dir)

    summary = _load_dict(run_dir / "run_summary.json")
    config = _load_dict(run_dir / "run_config.json")
    summary = _with_stale_status(summary, config, run_dir)
    instruction = str(summary.get("instruction") or config.get("instruction") or "Web Agent Report").strip()
    answer = str(summary.get("final_answer") or summary.get("final_reason") or "").strip()
    if not answer:
        answer = "Agent 还没有生成最终答案。"

    status = str(summary.get("final_status") or "unknown")
    success = summary.get("success")
    model = str(summary.get("model") or config.get("model") or "")
    provider = str(summary.get("provider") or config.get("provider") or "")
    steps_used = str(summary.get("steps_used") or _count_steps(run_dir))
    final_url = str(summary.get("final_url") or config.get("start_url") or "")
    report_error = str(summary.get("final_report_error") or "")
    title = _compact_report_title(instruction)
    gallery = _fallback_report_gallery(run_dir)
    metadata_cards = "".join(
        f'<div class="metric"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'
        for label, value in [
            ("状态", status),
            ("成功", "是" if success is True else "否" if success is False else "运行中"),
            ("步数", steps_used),
            ("模型", " / ".join(part for part in [provider, model] if part) or "--"),
        ]
    )
    report_note = ""
    if report_error:
        report_note = f'<p class="note">模型自动 HTML 报告生成失败，当前为后端兜底预览：{html.escape(report_error)}</p>'

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(title)} · Web-VLMAgent Report</title>
<style>
:root {{ color-scheme: light; --ink:#111612; --muted:#66736b; --line:rgba(20,36,29,.12); --paper:#fbfcf8; --card:rgba(255,255,255,.82); --teal:#2f7b72; --gold:#d8a84f; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:radial-gradient(circle at 10% 0%, #eff8f5 0, transparent 34%), linear-gradient(180deg, #fbfcf8 0%, #eef4ef 100%); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; line-height:1.65; }}
a {{ color:#0f6c8e; text-decoration-thickness:1px; text-underline-offset:3px; }}
.hero {{ min-height:58vh; display:grid; align-items:end; padding:64px min(7vw,96px); border-bottom:1px solid var(--line); background:linear-gradient(135deg, rgba(13,34,27,.92), rgba(47,123,114,.78)), url('/api/file?path={_fallback_hero_image(run_dir)}') center/cover; color:white; }}
.hero-inner {{ width:min(980px,100%); }}
.eyebrow {{ margin:0 0 14px; font-size:12px; letter-spacing:.18em; text-transform:uppercase; opacity:.82; font-weight:800; }}
h1 {{ margin:0; font-size:clamp(42px,7vw,92px); line-height:.98; letter-spacing:0; max-width:980px; }}
.subtitle {{ margin:24px 0 0; max-width:760px; font-size:clamp(18px,2.1vw,25px); color:rgba(255,255,255,.82); }}
.metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-top:34px; max-width:940px; }}
.metric {{ padding:16px 18px; border:1px solid rgba(255,255,255,.24); border-radius:24px; background:rgba(255,255,255,.12); backdrop-filter:blur(18px); }}
.metric span {{ display:block; color:rgba(255,255,255,.68); font-size:12px; font-weight:800; }}
.metric strong {{ display:block; margin-top:4px; font-size:18px; overflow-wrap:anywhere; }}
.page {{ width:min(1160px,calc(100vw - 34px)); margin:0 auto; padding:64px 0 84px; }}
.section-kicker {{ color:var(--teal); font-size:12px; font-weight:900; letter-spacing:.14em; text-transform:uppercase; }}
.panel {{ border:1px solid var(--line); border-radius:30px; background:var(--card); box-shadow:0 24px 70px rgba(23,48,38,.10); backdrop-filter:blur(20px); overflow:hidden; }}
.answer {{ padding:42px; font-size:18px; }}
.answer h1,.answer h2,.answer h3 {{ margin:34px 0 12px; line-height:1.18; }}
.answer h1:first-child,.answer h2:first-child,.answer h3:first-child {{ margin-top:0; }}
.answer h1 {{ font-size:34px; }} .answer h2 {{ font-size:28px; }} .answer h3 {{ font-size:22px; }}
.answer p {{ margin:12px 0; }} .answer li {{ margin:8px 0; }} .answer strong {{ font-weight:900; }}
.gallery {{ margin-top:28px; display:grid; grid-template-columns:1.2fr .8fr; gap:18px; }}
.shot {{ border:1px solid var(--line); border-radius:26px; background:white; overflow:hidden; box-shadow:0 18px 42px rgba(30,52,44,.09); }}
.shot img {{ display:block; width:100%; aspect-ratio:16/10; object-fit:cover; }}
.shot figcaption {{ padding:14px 16px; color:var(--muted); font-weight:750; }}
.sources {{ margin-top:28px; padding:28px; }}
.note {{ margin:24px 0 0; color:#8a5b12; background:#fff8e6; border:1px solid #f0db9c; border-radius:18px; padding:14px 16px; }}
@media (max-width:860px) {{ .metrics,.gallery {{ grid-template-columns:1fr; }} .hero {{ min-height:auto; padding:46px 24px; }} .answer {{ padding:26px; }} }}
</style>
</head>
<body>
<header class="hero">
  <div class="hero-inner">
    <p class="eyebrow">Web-VLMAgent · AI Generated Report</p>
    <h1>{html.escape(title)}</h1>
    <p class="subtitle">这是旧 trace 的兜底展示页。新任务成功结束后，页面内容和高级排版会由模型自动生成并保存为 final_report.html。</p>
    <div class="metrics">{metadata_cards}</div>
    {report_note}
  </div>
</header>
<main class="page">
  <p class="section-kicker">Final Answer</p>
  <section class="panel answer">{_markdownish_to_html(answer)}</section>
  {gallery}
  <section class="panel sources">
    <p class="section-kicker">Source / Trace</p>
    <p>最终 URL：{_linkify_plain(final_url or '--')}</p>
    <p>Trace：{html.escape(_rel(run_dir))}</p>
    <p><a href="/">返回工作台</a></p>
  </section>
</main>
</body>
</html>"""


def _fallback_report_gallery(run_dir: Path) -> str:
    images = [path for path in sorted(run_dir.glob("step_*/screenshot.png")) if path.is_file()]
    if not images:
        return ""
    selected = images[-4:]
    cards = []
    for path in selected:
        step = path.parent.name
        src = f"/api/file?path={_rel(path)}"
        cards.append(
            f'<figure class="shot"><img src="{html.escape(src, quote=True)}" alt="{html.escape(step)} screenshot"><figcaption>{html.escape(step)}</figcaption></figure>'
        )
    return f'<p class="section-kicker" style="margin-top:48px">Visual Evidence</p><section class="gallery">{"".join(cards)}</section>'


def _fallback_hero_image(run_dir: Path) -> str:
    images = [path for path in sorted(run_dir.glob("step_*/screenshot.png")) if path.is_file()]
    if not images:
        return ""
    return _rel(images[-1])


def _rewrite_generated_report_asset_paths(text: str, run_dir: Path) -> str:
    """Serve standalone reports through WebUI without breaking local relative images."""

    def to_api_path(raw_path: str) -> str:
        if not raw_path or raw_path.startswith(("#", "/", "http://", "https://", "data:", "mailto:")):
            return raw_path
        decoded = unquote(raw_path)
        if not re.fullmatch(r"step_\d{3}/(?:marked_)?screenshot\.png", decoded):
            return raw_path
        file_path = (run_dir / decoded).resolve()
        if not str(file_path).startswith(str(PROJECT_ROOT.resolve())) or not file_path.exists():
            return raw_path
        return f"/api/file?path={quote(_rel(file_path), safe='/')}"

    def replace_attr(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{to_api_path(match.group('path'))}{match.group('suffix')}"

    def replace_css_url(match: re.Match[str]) -> str:
        quote_char = match.group("quote") or ""
        return f"url({quote_char}{to_api_path(match.group('path'))}{quote_char})"

    text = re.sub(
        r"(?P<prefix>\b(?:src|href)\s*=\s*['\"])(?P<path>step_\d{3}/(?:marked_)?screenshot\.png)(?P<suffix>['\"])",
        replace_attr,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"url\((?P<quote>['\"]?)(?P<path>step_\d{3}/(?:marked_)?screenshot\.png)(?P=quote)\)",
        replace_css_url,
        text,
        flags=re.IGNORECASE,
    )
    return text


def _compact_report_title(instruction: str) -> str:
    text = re.sub(r"\s+", " ", instruction).strip()
    if not text:
        return "Web Agent Report"
    if "广州" in text and ("两天" in text or "2" in text):
        return "广州两日旅行攻略"
    if len(text) <= 28:
        return text
    return f"{text[:28]}..."


def _markdownish_to_html(text: str) -> str:
    blocks: list[str] = []
    list_type: str | None = None

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            blocks.append(f"</{list_type}>")
            list_type = None

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            close_list()
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            close_list()
            level = min(3, len(heading.group(1)))
            blocks.append(f"<h{level}>{_format_inline(heading.group(2))}</h{level}>")
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", line)
        if bullet:
            if list_type != "ul":
                close_list()
                list_type = "ul"
                blocks.append("<ul>")
            blocks.append(f"<li>{_format_inline(bullet.group(1))}</li>")
            continue

        numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if numbered:
            if list_type != "ol":
                close_list()
                list_type = "ol"
                blocks.append("<ol>")
            blocks.append(f"<li>{_format_inline(numbered.group(1))}</li>")
            continue

        close_list()
        blocks.append(f"<p>{_format_inline(line)}</p>")

    close_list()
    return "\n".join(blocks)


def _format_inline(text: str) -> str:
    result: list[str] = []
    link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
    cursor = 0
    for match in link_pattern.finditer(text):
        result.append(_linkify_plain(text[cursor : match.start()]))
        label = _apply_emphasis(html.escape(match.group(1)))
        href = html.escape(match.group(2), quote=True)
        result.append(f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>')
        cursor = match.end()
    result.append(_linkify_plain(text[cursor:]))
    return "".join(result)


def _linkify_plain(text: str) -> str:
    url_pattern = re.compile(r"https?://[^\s<>)]+")
    result: list[str] = []
    cursor = 0
    for match in url_pattern.finditer(text):
        result.append(_apply_emphasis(html.escape(text[cursor : match.start()])))
        raw_url = match.group(0)
        href = html.escape(raw_url, quote=True)
        label = html.escape(raw_url)
        result.append(f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>')
        cursor = match.end()
    result.append(_apply_emphasis(html.escape(text[cursor:])))
    return "".join(result)


def _apply_emphasis(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _normalize_chat_history(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    history: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        role = str(item.get("role", "agent")).strip().lower()
        if role in {"human", "user"}:
            normalized_role = "user"
        elif role in {"system", "status"}:
            normalized_role = "system"
        else:
            normalized_role = "agent"
        history.append({"role": normalized_role, "text": text})
    return history


def _append_chat_history(raw: Any, role: str, text: str) -> list[dict[str, str]]:
    history = _normalize_chat_history(raw)
    message = str(text or "").strip()
    if not message:
        return history
    normalized_role = "user" if role == "user" else "system" if role == "system" else "agent"
    if history and history[-1] == {"role": normalized_role, "text": message}:
        return history
    return [*history, {"role": normalized_role, "text": message}]


def _is_continue_reply(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return True
    return normalized in {
        "继续",
        "继续执行",
        "同意",
        "可以",
        "好的",
        "好",
        "ok",
        "yes",
        "go",
        "continue",
        "继续15步",
        "继续 15 步",
    }


def _continue_hint_from_reply(text: str) -> str:
    raw = text.strip()
    if not raw or _is_continue_reply(raw):
        return ""

    lowered = raw.lower()
    prefixes = [
        "继续执行",
        "继续 15 步",
        "继续15步",
        "continue",
        "继续",
        "同意",
        "可以",
        "好的",
        "好",
        "ok",
        "yes",
        "go",
    ]
    for prefix in prefixes:
        if lowered.startswith(prefix):
            hint = raw[len(prefix) :].lstrip(" ，,;；:：。.!！")
            for lead in ("但是", "不过", "然后", "并且", "请"):
                if hint.startswith(lead):
                    hint = hint[len(lead) :].lstrip(" ，,;；:：。.!！")
                    break
            return hint.strip()
    return raw


def _append_human_hint(raw_hints: Any, hint: str) -> list[str]:
    hints: list[str] = []
    if isinstance(raw_hints, list):
        hints = [str(item).strip() for item in raw_hints if str(item).strip()]
    clean_hint = hint.strip()
    if clean_hint and clean_hint not in hints:
        hints.append(clean_hint)
    return hints[-5:]


def _job_chat_history(job: dict[str, Any]) -> list[dict[str, str]]:
    metadata = job.get("task_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    summary = job.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    return (
        _normalize_chat_history(job.get("chat_history"))
        or _normalize_chat_history(summary.get("chat_history"))
        or _normalize_chat_history(metadata.get("chat_history"))
    )


def _final_chat_text(summary: dict[str, Any]) -> str:
    return str(
        summary.get("final_answer")
        or summary.get("answer")
        or summary.get("final_reason")
        or status_label_for_chat(summary)
    ).strip()


def status_label_for_chat(summary: dict[str, Any]) -> str:
    status = str(summary.get("final_status") or "done")
    if summary.get("success") is True:
        return "任务完成。"
    if status == "needs_user_input":
        return "我需要你补充一点信息。"
    if status == "fail":
        return "任务未完整完成，但已生成阶段性结果和 HTML 报告。"
    return f"运行结束：{status}"


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "on"}


def _count_steps(run_dir: Path) -> int:
    complete_steps = 0
    for path in sorted(run_dir.glob("step_*")):
        if not path.is_dir():
            continue
        if (path / "execution_result.json").exists():
            complete_steps += 1
    return complete_steps


def _load_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_path(raw_path: str) -> Path:
    decoded = unquote(raw_path).replace("\\", "/")
    path = (PROJECT_ROOT / decoded).resolve()
    if not str(path).startswith(str(PROJECT_ROOT.resolve())):
        raise ValueError("path is outside project root")
    return path


def _static_path(route: str) -> Path | None:
    raw = route.lstrip("/") or "index.html"
    path = (STATIC_DIR / raw).resolve()
    if not str(path).startswith(str(STATIC_DIR.resolve())):
        return None
    return path


def _rel(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()


def _rel_if_exists(path: Path) -> str | None:
    return _rel(path) if path.exists() else None


def _slug(value: str) -> str:
    slug = "".join(char if char.isalnum() else "_" for char in value).strip("_")
    return slug[:42] or "site"


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Web-VLMAgent workbench.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8601)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), WorkbenchHandler)
    print(f"Web-VLMAgent workbench running at http://{args.host}:{args.port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
