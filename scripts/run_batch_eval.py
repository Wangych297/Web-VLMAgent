from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.batch_runner import REPORT_DIR, run_batch  # noqa: E402
from eval.runner import TASK_TRACE_DIR  # noqa: E402
from scripts.run_task import include_screenshot_images, parse_bool  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 5 batch evaluation from a YAML task file.")
    parser.add_argument("--task_file", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--provider", choices=["deepseek", "openai", "gemini", "cdp"], default="deepseek")
    parser.add_argument("--model", default="", help="Optional model id for the selected provider.")
    parser.add_argument(
        "--observation_mode",
        choices=["auto", "vision", "text"],
        default="auto",
        help="Use vision to send screenshots, text to use DOM/accessibility/page text only.",
    )
    parser.add_argument(
        "--browser_backend",
        choices=["playwright", "web_access"],
        default="playwright",
        help="Browser backend. Use web_access for real browser/CDP control.",
    )
    parser.add_argument("--trace_dir", type=Path, default=TASK_TRACE_DIR)
    parser.add_argument("--report_dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--headless", type=parse_bool, default=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    batch_summary = run_batch(
        task_file=args.task_file,
        repeat=args.repeat,
        provider=args.provider,
        model=args.model or None,
        include_screenshot_images=include_screenshot_images(args.observation_mode),
        trace_dir=args.trace_dir,
        report_dir=args.report_dir,
        headless=args.headless,
        browser_backend=args.browser_backend,
    )
    metrics = batch_summary["metrics"]
    reports = batch_summary["reports"]

    print(f"batch_id: {batch_summary['batch_id']}")
    print(f"provider: {batch_summary['provider']}")
    print(f"model: {batch_summary['model']}")
    print(f"observation_mode: {batch_summary['observation_mode']}")
    print(f"run_count: {batch_summary['run_count']}")
    print(f"task_success_rate: {metrics['task_success_rate']}")
    print(f"average_steps: {metrics['average_steps']}")
    print(f"average_time: {metrics['average_time']}")
    print(f"action_json_validity_rate: {metrics['action_json_validity_rate']}")
    print(f"grounding_error_rate: {metrics['grounding_error_rate']}")
    print(f"correction_retry_success_rate: {metrics['correction_retry_success_rate']}")
    print(f"summary_csv: {reports['summary_csv']}")
    print(f"summary_md: {reports['summary_md']}")
    print(f"trace_report_html: {reports['trace_report_html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
