from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from browser.playwright_env import PlaywrightEnv  # noqa: E402
from eval.runner import write_json  # noqa: E402
from perception.som_marker import draw_set_of_marks  # noqa: E402


TRACE_DIR = Path("traces/som_demo")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Phase 2 Set-of-Marks demo.")
    parser.add_argument("--url", required=True, help="Start URL to open in Chromium.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    TRACE_DIR.mkdir(parents=True, exist_ok=True)

    screenshot_path = TRACE_DIR / "screenshot.png"
    candidates_path = TRACE_DIR / "candidates.json"
    marked_screenshot_path = TRACE_DIR / "marked_screenshot.png"

    with PlaywrightEnv() as env:
        env.open_url(args.url)
        env.screenshot(screenshot_path)

        candidates = env.candidates()
        write_json(candidates_path, [candidate.model_dump() for candidate in candidates])

    marked_candidates = draw_set_of_marks(
        screenshot_path=screenshot_path,
        candidates=candidates,
        output_path=marked_screenshot_path,
    )

    print(f"Trace directory: {TRACE_DIR}")
    print(f"Candidates: {len(candidates)}")
    print(f"Marked candidates: {len(marked_candidates)}")
    print(f"Marked screenshot: {marked_screenshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
