from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.batch_runner import load_tasks  # noqa: E402
from eval.metrics import collect_run_records, compute_metrics  # noqa: E402
from eval.report_generator import generate_reports  # noqa: E402
from eval.runner import TASK_TRACE_DIR, run_task, write_json  # noqa: E402
from scripts.run_task import parse_bool  # noqa: E402


@dataclass(frozen=True)
class ModelSpec:
    name: str
    provider: str
    model: str
    observation_mode: str

    @property
    def include_screenshot_images(self) -> bool:
        return self.observation_mode == "vision"


DEFAULT_MODEL_SPECS = [
    ModelSpec("gemini_3_5_flash_nothinking_vision", "gemini", "gemini-3.5-flash-nothinking", "vision"),
    ModelSpec("claude_opus_4_8_vision", "openai", "claude-opus-4-8", "vision"),
    ModelSpec("qwen3_vl_235b_vision", "openai", "qwen3-vl-235b-a22b-instruct", "vision"),
    ModelSpec("doubao_vision_pro_vision", "openai", "doubao-1-5-vision-pro-32k", "vision"),
    ModelSpec("qwen3_7_max_text", "openai", "qwen3.7-max", "text"),
    ModelSpec("deepseek_v4_pro_text", "openai", "DeepSeek-V4-Pro", "text"),
    ModelSpec("kimi_k2_6_text", "openai", "kimi-k2.6", "text"),
    ModelSpec("glm_5_2_text", "openai", "glm-5.2", "text"),
    ModelSpec("minimax_m2_7_text", "openai", "MiniMax-M2.7", "text"),
    ModelSpec("ernie_x1_turbo_text", "openai", "ERNIE-X1-Turbo", "text"),
]

OFFICIAL_20260622_MODEL_SPECS = [
    ModelSpec("gpt_4o_mini_vision", "openai", "gpt-4o-mini", "vision"),
    ModelSpec("gpt_5_mini_vision", "openai", "gpt-5-mini", "vision"),
    ModelSpec("gemini_2_5_flash_vision", "gemini", "gemini-2.5-flash", "vision"),
    ModelSpec("claude_haiku_4_5_vision", "openai", "claude-haiku-4-5-20251001", "vision"),
    ModelSpec("qwen3_vl_32b_vision", "openai", "qwen3-vl-32b-instruct", "vision"),
    ModelSpec("doubao_vision_pro_vision", "openai", "doubao-1-5-vision-pro-32k", "vision"),
    ModelSpec("deepseek_v4_pro_text", "openai", "DeepSeek-V4-Pro", "text"),
    ModelSpec("qwen3_7_max_text", "openai", "qwen3.7-max", "text"),
    ModelSpec("kimi_k2_6_text", "openai", "kimi-k2.6", "text"),
    ModelSpec("glm_5_2_text", "openai", "GLM-5.2", "text"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a 10-model Web-VLMAgent evaluation suite.")
    parser.add_argument(
        "--task_file",
        type=Path,
        action="append",
        default=[],
        help="Task YAML file. Repeat this flag for multiple files.",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--trace_dir", type=Path, default=TASK_TRACE_DIR / "model_suite")
    parser.add_argument("--report_dir", type=Path, default=Path("reports") / "model_suite")
    parser.add_argument(
        "--model_set",
        choices=["default", "official_20260622"],
        default="default",
        help="Model suite preset to run.",
    )
    parser.add_argument(
        "--browser_backend",
        choices=["playwright", "web_access"],
        default="playwright",
        help="Browser backend. Use web_access for real browser/CDP control.",
    )
    parser.add_argument("--headless", type=parse_bool, default=True)
    parser.add_argument(
        "--only_model",
        action="append",
        default=[],
        help="Run only selected model spec name(s), useful for smoke tests.",
    )
    parser.add_argument(
        "--only_task",
        action="append",
        default=[],
        help="Run only selected task_id(s), useful for smoke tests.",
    )
    parser.add_argument(
        "--suite_tag",
        default="",
        help="Optional tag appended to the suite directory, useful when merging parallel model runs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ.setdefault("WEB_MMAGENT_SKIP_FINAL_REPORT", "1")
    task_files = args.task_file or [
        Path("tasks/csrankings.yaml"),
        Path("tasks/guangzhou_travel.yaml"),
        Path("tasks/bilibili.yaml"),
    ]
    model_specs = _selected_models(args.only_model, args.model_set)
    tasks = _selected_tasks(task_files, args.only_task)

    if not tasks:
        raise SystemExit("No tasks selected.")
    if not model_specs:
        raise SystemExit("No models selected.")

    suite_id_parts = [datetime.now().strftime("%Y%m%d_%H%M%S_%f")]
    if args.suite_tag:
        suite_id_parts.append(_safe_name(args.suite_tag))
    if len(model_specs) == 1:
        suite_id_parts.append(_safe_name(model_specs[0].name))
    suite_id = "_".join(part for part in suite_id_parts if part)
    suite_trace_dir = args.trace_dir / f"suite_{suite_id}"
    suite_report_dir = args.report_dir / f"suite_{suite_id}"
    suite_trace_dir.mkdir(parents=True, exist_ok=True)
    suite_report_dir.mkdir(parents=True, exist_ok=True)

    run_dirs: list[Path] = []
    run_summaries: list[dict[str, Any]] = []
    suite_config = {
        "suite_id": suite_id,
        "task_files": [str(path) for path in task_files],
        "task_count": len(tasks),
        "repeat": args.repeat,
        "models": [spec.__dict__ for spec in model_specs],
        "browser_backend": args.browser_backend,
        "headless": args.headless,
        "trace_dir": str(suite_trace_dir),
        "report_dir": str(suite_report_dir),
    }
    write_json(suite_report_dir / "suite_config.json", suite_config)

    for spec in model_specs:
        for task in tasks:
            for run_index in range(1, args.repeat + 1):
                run_id = f"{spec.name}__{task.task_id}_{run_index:03d}"
                print(f"[suite {suite_id}] start {run_id}", flush=True)
                metadata = {
                    "task_id": task.task_id,
                    "site": task.site,
                    "difficulty": task.difficulty,
                    "success_criteria": task.success_criteria,
                    "forbidden_actions": task.forbidden_actions,
                    "suite_id": suite_id,
                    "model_spec": spec.name,
                    "provider": spec.provider,
                    "model": spec.model,
                    "observation_mode": spec.observation_mode,
                    "run_index": run_index,
                }
                try:
                    summary = run_task(
                        start_url=task.start_url,
                        instruction=task.instruction,
                        max_steps=task.max_steps,
                        provider=spec.provider,
                        model=spec.model,
                        trace_dir=suite_trace_dir,
                        headless=args.headless,
                        run_id=run_id,
                        task_metadata=metadata,
                        browser_backend=args.browser_backend,
                        include_screenshot_images=spec.include_screenshot_images,
                        ask_user_on_failure=True,
                    )
                except Exception as exc:
                    summary = {
                        "run_id": run_id,
                        "success": False,
                        "final_status": "fatal_error",
                        "steps_used": 0,
                        "trace_dir": str(suite_trace_dir / run_id),
                        "final_reason": f"{exc.__class__.__name__}: {exc}",
                        "task_metadata": metadata,
                        "provider": spec.provider,
                        "model": spec.model,
                        "include_screenshot_images": spec.include_screenshot_images,
                    }
                    run_dir = suite_trace_dir / run_id
                    run_dir.mkdir(parents=True, exist_ok=True)
                    write_json(run_dir / "run_summary.json", summary)
                print(
                    f"[suite {suite_id}] done {run_id} status={summary.get('final_status')} success={summary.get('success')}",
                    flush=True,
                )
                run_dir = Path(summary["trace_dir"])
                _write_validator_result(run_dir)
                run_dirs.append(run_dir)
                run_summaries.append(summary)
                write_json(suite_report_dir / "suite_progress.json", {"runs": run_summaries})

    records = collect_run_records(run_dirs)
    metrics = compute_metrics(records)
    reports = generate_reports(records, metrics, suite_report_dir)
    suite_summary = {
        **suite_config,
        "run_count": len(records),
        "metrics": metrics,
        "reports": reports,
        "runs": run_summaries,
    }
    write_json(suite_report_dir / "suite_summary.json", suite_summary)
    print(f"suite_id: {suite_id}")
    print(f"run_count: {len(records)}")
    print(f"task_success_rate: {metrics['task_success_rate']}")
    print(f"summary_csv: {reports['summary_csv']}")
    print(f"summary_md: {reports['summary_md']}")
    print(f"trace_report_html: {reports['trace_report_html']}")
    return 0


def _selected_models(names: list[str], model_set: str = "default") -> list[ModelSpec]:
    specs = OFFICIAL_20260622_MODEL_SPECS if model_set == "official_20260622" else DEFAULT_MODEL_SPECS
    if not names:
        return specs
    wanted = set(names)
    return [spec for spec in specs if spec.name in wanted]


def _write_validator_result(run_dir: Path) -> None:
    try:
        from eval.validators import evaluate_run

        result = evaluate_run(run_dir)
        write_json(run_dir / "validator_result.json", result)
    except Exception as exc:
        write_json(
            run_dir / "validator_result.json",
            {
                "success": None,
                "needs_review": True,
                "evaluator": "validator_error",
                "reason": f"{exc.__class__.__name__}: {exc}",
            },
        )


def _selected_tasks(task_files: list[Path], task_ids: list[str]) -> list[Any]:
    tasks = []
    wanted = set(task_ids)
    for task_file in task_files:
        for task in load_tasks(task_file):
            if not wanted or task.task_id in wanted:
                tasks.append(task)
    return tasks


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value)


if __name__ == "__main__":
    raise SystemExit(main())
