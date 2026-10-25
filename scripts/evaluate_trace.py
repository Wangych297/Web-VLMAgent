from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.validators import evaluate_run, write_json  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a completed Web-VLMAgent trace against task criteria.")
    parser.add_argument("--run_dir", type=Path, required=True, help="Trace run directory containing run_summary.json.")
    parser.add_argument("--task_file", type=Path, default=None, help="Optional YAML task file for expected answers.")
    parser.add_argument(
        "--no_write",
        action="store_true",
        help="Print the validation result without writing validator_result.json.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = evaluate_run(args.run_dir, task_file=args.task_file)
    if not args.no_write:
        write_json(args.run_dir / "validator_result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    success = result.get("success")
    if success is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
