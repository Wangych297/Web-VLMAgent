from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.runner import TASK_TRACE_DIR, run_task  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Phase 4 multi-step Web-VLMAgent task.")
    parser.add_argument("--url", required=True, help="Start URL.")
    parser.add_argument("--task", required=True, help="Natural language task instruction.")
    parser.add_argument("--max_steps", type=int, default=10, help="Maximum number of agent steps.")
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
    parser.add_argument(
        "--web_access_browser",
        choices=["chrome", "edge"],
        default=None,
        help="Optional one-run browser override for web_access.",
    )
    parser.add_argument("--trace_dir", type=Path, default=TASK_TRACE_DIR)
    parser.add_argument("--headless", type=parse_bool, default=True)
    return parser.parse_args()


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("headless must be true or false")


def include_screenshot_images(mode: str) -> bool | None:
    if mode == "vision":
        return True
    if mode == "text":
        return False
    return None


def main() -> int:
    args = parse_args()
    summary = run_task(
        start_url=args.url,
        instruction=args.task,
        max_steps=args.max_steps,
        provider=args.provider,
        model=args.model or None,
        trace_dir=args.trace_dir,
        headless=args.headless,
        browser_backend=args.browser_backend,
        web_access_browser=args.web_access_browser,
        include_screenshot_images=include_screenshot_images(args.observation_mode),
    )

    print(f"run_id: {summary['run_id']}")
    print(f"success: {summary['success']}")
    print(f"final_status: {summary['final_status']}")
    print(f"steps_used: {summary['steps_used']}")
    print(f"trace_dir: {summary['trace_dir']}")
    print(f"final_reason: {summary['final_reason']}")

    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
