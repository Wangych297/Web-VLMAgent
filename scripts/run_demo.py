from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.runner import TRACE_DIR, run_demo  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Phase 1 Web-VLMAgent demo.")
    parser.add_argument("--url", required=True, help="Start URL to open in Chromium.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = run_demo(args.url, TRACE_DIR)
    result = summary["execution_result"]

    print(f"Trace directory: {summary['trace_dir']}")
    print(f"Candidates: {summary['candidate_count']}")
    print(f"Action: {summary['action']}")
    print(f"Execution ok: {result['ok']}")

    if not result["ok"]:
        print(f"Error type: {result['error_type']}")
        print(f"Error: {result['error']}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
