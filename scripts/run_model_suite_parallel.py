from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.runner import write_json  # noqa: E402
from scripts.run_model_suite import DEFAULT_MODEL_SPECS, OFFICIAL_20260622_MODEL_SPECS, parse_bool  # noqa: E402


@dataclass
class RunningProcess:
    model_name: str
    process: subprocess.Popen[str]
    stdout_path: Path
    stderr_path: Path
    started_at: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run model-suite evaluations with limited parallelism.")
    parser.add_argument("--suite_tag", default="")
    parser.add_argument("--max_workers", type=int, default=2)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--task_file", type=Path, action="append", default=[])
    parser.add_argument("--only_task", action="append", default=[])
    parser.add_argument("--headless", type=parse_bool, default=True)
    parser.add_argument("--browser_backend", choices=["playwright", "web_access"], default="playwright")
    parser.add_argument(
        "--model_set",
        choices=["default", "official_20260622"],
        default="default",
        help="Model suite preset forwarded to run_model_suite.py.",
    )
    parser.add_argument("--only_model", action="append", default=[])
    parser.add_argument("--report_root", type=Path, default=Path("reports"))
    parser.add_argument(
        "--trace_dir",
        type=Path,
        default=None,
        help="Optional trace root passed through to run_model_suite.py.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_workers < 1:
        raise SystemExit("--max_workers must be >= 1")

    suite_tag = args.suite_tag or f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    report_root = args.report_root
    report_root.mkdir(parents=True, exist_ok=True)
    status_path = report_root / f"model_suite_parallel_{_safe_name(suite_tag)}_status.json"
    model_names = _selected_model_names(args.only_model, args.model_set)

    pending = list(model_names)
    running: list[RunningProcess] = []
    completed: list[dict[str, Any]] = []
    started_at = datetime.now().isoformat(timespec="seconds")

    _write_status(status_path, suite_tag, started_at, pending, running, completed, "running")
    while pending or running:
        while pending and len(running) < args.max_workers:
            model_name = pending.pop(0)
            running.append(_start_model_run(args, suite_tag, model_name, report_root))
            _write_status(status_path, suite_tag, started_at, pending, running, completed, "running")

        time.sleep(10)
        still_running: list[RunningProcess] = []
        for item in running:
            returncode = item.process.poll()
            if returncode is None:
                still_running.append(item)
                continue
            completed.append(
                {
                    "model": item.model_name,
                    "returncode": returncode,
                    "stdout": str(item.stdout_path),
                    "stderr": str(item.stderr_path),
                    "started_at": item.started_at,
                    "ended_at": datetime.now().isoformat(timespec="seconds"),
                    "ok": returncode == 0,
                }
            )
        running = still_running
        _write_status(status_path, suite_tag, started_at, pending, running, completed, "running")

    combined = _collect_reports(suite_tag, report_root)
    failed = [item for item in completed if not item["ok"]]
    status = "failed" if failed else "complete"
    _write_status(status_path, suite_tag, started_at, pending, running, completed, status, combined)
    print(f"suite_tag: {suite_tag}")
    print(f"models: {len(completed)}")
    print(f"failed_models: {len(failed)}")
    print(f"status_json: {status_path}")
    if combined:
        print(f"combined_report_dir: {combined.get('output_dir', '')}")
    return 1 if failed else 0


def _selected_model_names(selected: list[str], model_set: str = "default") -> list[str]:
    specs = OFFICIAL_20260622_MODEL_SPECS if model_set == "official_20260622" else DEFAULT_MODEL_SPECS
    names = [spec.name for spec in specs]
    if not selected:
        return names
    wanted = set(selected)
    return [name for name in names if name in wanted]


def _start_model_run(
    args: argparse.Namespace,
    suite_tag: str,
    model_name: str,
    report_root: Path,
) -> RunningProcess:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stdout_path = report_root / f"model_suite_{_safe_name(suite_tag)}_{model_name}_{stamp}.log"
    stderr_path = report_root / f"model_suite_{_safe_name(suite_tag)}_{model_name}_{stamp}.err.log"
    command = [
        sys.executable,
        "scripts/run_model_suite.py",
        "--only_model",
        model_name,
        "--repeat",
        str(args.repeat),
        "--headless",
        str(args.headless).lower(),
        "--browser_backend",
        args.browser_backend,
        "--model_set",
        args.model_set,
        "--suite_tag",
        suite_tag,
    ]
    if args.trace_dir is not None:
        command.extend(["--trace_dir", str(args.trace_dir)])
    for task_file in args.task_file:
        command.extend(["--task_file", str(task_file)])
    for task_id in args.only_task:
        command.extend(["--only_task", task_id])
    stdout_file = stdout_path.open("w", encoding="utf-8", errors="replace")
    stderr_file = stderr_path.open("w", encoding="utf-8", errors="replace")
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=stdout_file,
        stderr=stderr_file,
        text=True,
    )
    return RunningProcess(
        model_name=model_name,
        process=process,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        started_at=datetime.now().isoformat(timespec="seconds"),
    )


def _collect_reports(suite_tag: str, report_root: Path) -> dict[str, Any]:
    output_dir = report_root / "model_suite" / f"combined_{_safe_name(suite_tag)}"
    command = [
        sys.executable,
        "scripts/collect_model_suite_reports.py",
        "--tag",
        suite_tag,
        "--output_dir",
        str(output_dir),
    ]
    result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "output_dir": str(output_dir),
    }


def _write_status(
    path: Path,
    suite_tag: str,
    started_at: str,
    pending: list[str],
    running: list[RunningProcess],
    completed: list[dict[str, Any]],
    status: str,
    combined: dict[str, Any] | None = None,
) -> None:
    payload = {
        "suite_tag": suite_tag,
        "status": status,
        "started_at": started_at,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "pending": pending,
        "running": [
            {
                "model": item.model_name,
                "pid": item.process.pid,
                "stdout": str(item.stdout_path),
                "stderr": str(item.stderr_path),
                "started_at": item.started_at,
            }
            for item in running
        ],
        "completed": completed,
    }
    if combined is not None:
        payload["combined"] = combined
    write_json(path, payload)


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value)


if __name__ == "__main__":
    raise SystemExit(main())
