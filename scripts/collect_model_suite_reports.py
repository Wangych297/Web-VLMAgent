from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.metrics import collect_run_records, compute_metrics, load_json  # noqa: E402
from eval.report_generator import generate_reports  # noqa: E402
from eval.runner import write_json  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge reports from parallel model-suite runs.")
    parser.add_argument("--suite_root", type=Path, default=Path("reports") / "model_suite")
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Only merge suite directories containing this tag. Repeat to merge multiple tags.",
    )
    parser.add_argument("--output_dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suite_dirs = _find_suite_dirs(args.suite_root, args.tag)
    if not suite_dirs:
        raise SystemExit(f"No suite directories found under {args.suite_root} for tag={args.tag!r}.")

    output_dir = args.output_dir or _default_output_dir(args.suite_root, args.tag)
    run_dirs = _collect_run_dirs(suite_dirs)
    records = collect_run_records(run_dirs)
    metrics = compute_metrics(records)
    reports = generate_reports(records, metrics, output_dir)
    group_reports = _write_group_reports(output_dir, records)

    summary = {
        "suite_dirs": [str(path) for path in suite_dirs],
        "run_count": len(records),
        "metrics": metrics,
        "reports": {**reports, **group_reports},
    }
    write_json(output_dir / "combined_summary.json", summary)

    print(f"merged_suites: {len(suite_dirs)}")
    print(f"run_count: {len(records)}")
    print(f"task_success_rate: {metrics['task_success_rate']}")
    print(f"summary_csv: {reports['summary_csv']}")
    print(f"model_summary_csv: {group_reports['model_summary_csv']}")
    print(f"task_summary_csv: {group_reports['task_summary_csv']}")
    print(f"trace_report_html: {reports['trace_report_html']}")
    return 0


def _find_suite_dirs(suite_root: Path, tags: list[str]) -> list[Path]:
    if not suite_root.exists():
        return []
    suite_dirs = [path for path in suite_root.glob("suite_*") if path.is_dir()]
    if tags:
        suite_dirs = [path for path in suite_dirs if any(tag in path.name for tag in tags)]
    return sorted(suite_dirs, key=lambda path: path.name)


def _default_output_dir(suite_root: Path, tags: list[str]) -> Path:
    suffix = "_".join(_safe_name(tag) for tag in tags) if tags else datetime.now().strftime("%Y%m%d_%H%M%S")
    return suite_root / f"combined_{suffix}"


def _collect_run_dirs(suite_dirs: list[Path]) -> list[Path]:
    run_dirs: list[Path] = []
    seen: set[Path] = set()
    for suite_dir in suite_dirs:
        summary = load_json(suite_dir / "suite_summary.json", {})
        progress = load_json(suite_dir / "suite_progress.json", {})
        config = load_json(suite_dir / "suite_config.json", {})
        runs = summary.get("runs") or progress.get("runs") or []
        for run in runs:
            trace_dir = run.get("trace_dir")
            if not trace_dir:
                continue
            run_dir = Path(trace_dir)
            if run_dir.exists() and run_dir not in seen:
                run_dirs.append(run_dir)
                seen.add(run_dir)
        trace_root = config.get("trace_dir")
        if trace_root:
            for summary_path in Path(trace_root).glob("*/run_summary.json"):
                run_dir = summary_path.parent
                if run_dir.exists() and run_dir not in seen:
                    run_dirs.append(run_dir)
                    seen.add(run_dir)
    return run_dirs


def _write_group_reports(output_dir: Path, records: list[dict[str, Any]]) -> dict[str, str]:
    model_rows = _group_records(records, ["model", "observation_mode"])
    task_rows = _group_records(records, ["task_id", "site", "difficulty"])
    model_path = output_dir / "model_summary.csv"
    task_path = output_dir / "task_summary.csv"
    _write_group_csv(model_path, model_rows)
    _write_group_csv(task_path, task_rows)
    _write_group_md(output_dir / "model_summary.md", "Model Summary", model_rows)
    _write_group_md(output_dir / "task_summary.md", "Task Summary", task_rows)
    return {
        "model_summary_csv": str(model_path),
        "task_summary_csv": str(task_path),
        "model_summary_md": str(output_dir / "model_summary.md"),
        "task_summary_md": str(output_dir / "task_summary.md"),
    }


def _group_records(records: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[tuple(record.get(key, "") for key in keys)].append(record)

    rows: list[dict[str, Any]] = []
    for values, group in sorted(grouped.items(), key=lambda item: item[0]):
        metrics = compute_metrics(group)
        row = {key: value for key, value in zip(keys, values)}
        row.update(
            {
                "total_runs": metrics["total_runs"],
                "success_runs": metrics["success_runs"],
                "task_success_rate": metrics["task_success_rate"],
                "average_steps": metrics["average_steps"],
                "average_time": metrics["average_time"],
                "action_json_validity_rate": metrics["action_json_validity_rate"],
                "grounding_error_rate": metrics["grounding_error_rate"],
                "failure_type_distribution": json.dumps(
                    metrics["failure_type_distribution"],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )
        rows.append(row)
    return rows


def _write_group_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_group_md(path: Path, title: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text(f"# {title}\n\nNo records.\n", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    lines = [f"# {title}", "", "| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(row.get(header, "")) for header in headers) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value)


if __name__ == "__main__":
    raise SystemExit(main())
