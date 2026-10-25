from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from eval.metrics import collect_run_records, compute_metrics
from eval.report_generator import generate_reports
from eval.runner import TASK_TRACE_DIR, run_task, write_json


REPORT_DIR = Path("reports")


@dataclass(frozen=True)
class BatchTask:
    task_id: str
    site: str
    difficulty: str
    start_url: str
    instruction: str
    max_steps: int
    success_criteria: dict[str, Any]
    forbidden_actions: list[str]


def load_tasks(task_file: Path) -> list[BatchTask]:
    with task_file.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if isinstance(data, dict) and "tasks" in data:
        raw_tasks = data["tasks"]
    else:
        raw_tasks = data

    if not isinstance(raw_tasks, list):
        raise ValueError("task file must contain a list of tasks or a top-level 'tasks' list")

    return [parse_task(raw_task) for raw_task in raw_tasks]


def parse_task(raw_task: dict[str, Any]) -> BatchTask:
    required = ["task_id", "site", "difficulty", "start_url", "instruction", "max_steps"]
    missing = [key for key in required if key not in raw_task]
    if missing:
        raise ValueError(f"task is missing required fields: {missing}")

    return BatchTask(
        task_id=str(raw_task["task_id"]),
        site=str(raw_task["site"]),
        difficulty=str(raw_task["difficulty"]),
        start_url=str(raw_task["start_url"]),
        instruction=str(raw_task["instruction"]),
        max_steps=int(raw_task["max_steps"]),
        success_criteria=dict(raw_task.get("success_criteria") or {}),
        forbidden_actions=list(raw_task.get("forbidden_actions") or []),
    )


def run_batch(
    task_file: Path,
    repeat: int,
    provider: str,
    model: str | None = None,
    include_screenshot_images: bool | None = None,
    trace_dir: Path = TASK_TRACE_DIR,
    report_dir: Path = REPORT_DIR,
    headless: bool = True,
    browser_backend: str = "playwright",
) -> dict[str, Any]:
    tasks = load_tasks(task_file)
    resolved_include_screenshot_images = (
        include_screenshot_images if include_screenshot_images is not None else provider in {"openai", "gemini", "cdp"}
    )
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_report_dir = report_dir / f"batch_{batch_id}"
    batch_report_dir.mkdir(parents=True, exist_ok=True)

    run_dirs: list[Path] = []
    run_summaries: list[dict[str, Any]] = []

    for task in tasks:
        for run_index in range(1, repeat + 1):
            run_id = f"{task.task_id}_{run_index:03d}"
            metadata = {
                "task_id": task.task_id,
                "site": task.site,
                "difficulty": task.difficulty,
                "success_criteria": task.success_criteria,
                "forbidden_actions": task.forbidden_actions,
                "task_file": str(task_file),
                "batch_id": batch_id,
                "run_index": run_index,
                "provider": provider,
                "model": model or "",
                "observation_mode": "vision" if resolved_include_screenshot_images else "text",
            }
            summary = run_task(
                start_url=task.start_url,
                instruction=task.instruction,
                max_steps=task.max_steps,
                provider=provider,
                model=model,
                trace_dir=trace_dir,
                headless=headless,
                run_id=run_id,
                task_metadata=metadata,
                browser_backend=browser_backend,
                include_screenshot_images=resolved_include_screenshot_images,
            )
            run_dir = Path(summary["trace_dir"])
            run_dirs.append(run_dir)
            run_summaries.append(summary)

    records = collect_run_records(run_dirs)
    metrics = compute_metrics(records)
    reports = generate_reports(records, metrics, batch_report_dir)
    batch_summary = {
        "batch_id": batch_id,
        "task_file": str(task_file),
        "repeat": repeat,
        "provider": provider,
        "model": model or "",
        "observation_mode": "vision" if resolved_include_screenshot_images else "text",
        "browser_backend": browser_backend,
        "trace_dir": str(trace_dir),
        "report_dir": str(batch_report_dir),
        "run_count": len(records),
        "metrics": metrics,
        "reports": reports,
        "runs": run_summaries,
    }
    write_json(batch_report_dir / "batch_summary.json", batch_summary)
    return batch_summary
