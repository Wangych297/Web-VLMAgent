from __future__ import annotations

import csv
import html
import json
import os
from pathlib import Path
from typing import Any

from eval.metrics import load_json


def generate_reports(
    records: list[dict[str, Any]],
    metrics: dict[str, Any],
    report_dir: Path,
) -> dict[str, str]:
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / "summary.csv"
    md_path = report_dir / "summary.md"
    html_path = report_dir / "trace_report.html"

    write_summary_csv(csv_path, records)
    write_summary_md(md_path, records, metrics)
    write_trace_html(html_path, records)

    return {
        "summary_csv": str(csv_path),
        "summary_md": str(md_path),
        "trace_report_html": str(html_path),
    }


def write_summary_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fieldnames = [
        "task_id",
        "site",
        "difficulty",
        "run_id",
        "provider",
        "model",
        "observation_mode",
        "success",
        "harness_success",
        "validator_success",
        "validator_needs_review",
        "validated_success",
        "validator_score",
        "validator_evaluator",
        "final_status",
        "steps_used",
        "duration_seconds",
        "action_json_valid_steps",
        "total_steps",
        "grounding_errors",
        "retry_used",
        "failure_type",
        "trace_dir",
        "final_reason",
        "validator_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fieldnames})


def write_summary_md(path: Path, records: list[dict[str, Any]], metrics: dict[str, Any]) -> None:
    lines = [
        "# Batch Evaluation Summary",
        "",
        "## Metrics",
        "",
        f"- Total runs: {metrics['total_runs']}",
        f"- Success runs: {metrics['success_runs']}",
        f"- Task success rate: {metrics['task_success_rate']}",
        f"- Validated success runs: {metrics.get('validated_success_runs', 0)}",
        f"- Validated success rate: {metrics.get('validated_success_rate', 0.0)}",
        f"- Validator evaluated runs: {metrics.get('validator_evaluated_runs', 0)}",
        f"- Validator binary runs: {metrics.get('validator_binary_runs', 0)}",
        f"- Validator review-needed runs: {metrics.get('validator_review_runs', 0)}",
        f"- Validator success rate: {metrics.get('validator_success_rate', 0.0)}",
        f"- Average steps: {metrics['average_steps']}",
        f"- Average time: {metrics['average_time']} seconds",
        f"- Action JSON validity rate: {metrics['action_json_validity_rate']}",
        f"- Grounding error rate: {metrics['grounding_error_rate']}",
        f"- Correction / retry success rate: {metrics['correction_retry_success_rate']}",
        f"- Retry runs: {metrics['retry_runs']}",
        "",
        "## Failure Type Distribution",
        "",
    ]
    for failure_type, count in metrics["failure_type_distribution"].items():
        lines.append(f"- {failure_type}: {count}")

    lines.extend(
        [
            "",
            "## Runs",
            "",
            "| task_id | run_id | model | obs | harness_success | validator_success | needs_review | validated_success | validator_score | status | steps | seconds | failure_type | trace |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---|---|",
        ]
    )
    for record in records:
        lines.append(
            "| {task_id} | {run_id} | {model} | {obs} | {harness_success} | {validator_success} | {needs_review} | {validated_success} | {validator_score} | {status} | {steps} | {seconds} | {failure_type} | {trace} |".format(
                task_id=record.get("task_id", ""),
                run_id=record.get("run_id", ""),
                model=record.get("model", ""),
                obs=record.get("observation_mode", ""),
                harness_success=record.get("harness_success", record.get("success", "")),
                validator_success=record.get("validator_success", ""),
                needs_review=record.get("validator_needs_review", ""),
                validated_success=record.get("validated_success", record.get("success", "")),
                validator_score=record.get("validator_score", ""),
                status=record.get("final_status", ""),
                steps=record.get("steps_used", ""),
                seconds=record.get("duration_seconds", ""),
                failure_type=record.get("failure_type", ""),
                trace=record.get("trace_dir", ""),
            )
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_trace_html(path: Path, records: list[dict[str, Any]]) -> None:
    parts = [
        "<!doctype html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>Web-VLMAgent Trace Report</title>",
        "<style>",
        "body{font-family:Arial,sans-serif;margin:24px;line-height:1.4;color:#17202a}",
        "details{border:1px solid #ccd1d1;border-radius:6px;margin:12px 0;padding:10px}",
        "summary{cursor:pointer;font-weight:700}",
        ".grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}",
        "img{max-width:100%;border:1px solid #d5dbdb;border-radius:4px}",
        "pre{background:#f4f6f7;padding:10px;border-radius:4px;overflow:auto;max-height:360px}",
        ".ok{color:#117a65}.fail{color:#b03a2e}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>Web-VLMAgent Trace Report</h1>",
    ]

    for record in records:
        run_dir = Path(str(record["trace_dir"]))
        status_class = "ok" if record["success"] else "fail"
        parts.append(
            f"<details open><summary>{_esc(record['run_id'])} "
            f"<span class='{status_class}'>success={_esc(record['success'])}</span></summary>"
        )
        parts.append("<ul>")
        parts.append(f"<li>task_id: {_esc(record.get('task_id', ''))}</li>")
        parts.append(f"<li>status: {_esc(record.get('final_status', ''))}</li>")
        parts.append(f"<li>steps: {_esc(record.get('steps_used', ''))}</li>")
        parts.append(f"<li>reason: {_esc(record.get('final_reason', ''))}</li>")
        parts.append("</ul>")

        steps_used = int(record.get("steps_used") or 0)
        if steps_used > 0:
            step_dirs = [run_dir / f"step_{index:03d}" for index in range(steps_used)]
        else:
            step_dirs = sorted(path for path in run_dir.glob("step_*") if path.is_dir())
        for step_dir in step_dirs:
            parts.append(_step_html(report_path=path, step_dir=step_dir))

        parts.append("</details>")

    parts.extend(["</body>", "</html>"])
    path.write_text("\n".join(parts), encoding="utf-8")


def _step_html(report_path: Path, step_dir: Path) -> str:
    parsed_action = load_json(step_dir / "parsed_action.json", {})
    execution_result = load_json(step_dir / "execution_result.json", {})
    progress_check = load_json(step_dir / "progress_check.json", {})
    final_verification = load_json(step_dir / "final_verification.json", {})
    candidates = load_json(step_dir / "candidates.json", [])

    screenshot = _rel(report_path.parent, step_dir / "screenshot.png")
    marked = _rel(report_path.parent, step_dir / "marked_screenshot.png")

    payload = {
        "parsed_action": parsed_action,
        "execution_result": execution_result,
        "progress_check": progress_check,
        "final_verification": final_verification,
        "candidate_count": len(candidates),
        "candidates_preview": candidates[:10],
    }

    return "\n".join(
        [
            f"<details><summary>{_esc(step_dir.name)} action={_esc(parsed_action.get('action', ''))}</summary>",
            "<div class='grid'>",
            f"<div><h3>screenshot</h3><img src='{_esc(screenshot)}'></div>",
            f"<div><h3>marked_screenshot</h3><img src='{_esc(marked)}'></div>",
            "</div>",
            f"<pre>{_esc(json.dumps(payload, ensure_ascii=False, indent=2))}</pre>",
            "</details>",
        ]
    )


def _rel(base_dir: Path, target: Path) -> str:
    try:
        return os.path.relpath(target.resolve(), base_dir.resolve()).replace("\\", "/")
    except ValueError:
        return target.resolve().as_uri()


def _esc(value: Any) -> str:
    return html.escape(str(value))
