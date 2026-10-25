from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def load_task_definitions(task_file: Path) -> dict[str, dict[str, Any]]:
    with task_file.open("r", encoding="utf-8") as file:
        payload = yaml.safe_load(file)
    raw_tasks = payload.get("tasks", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_tasks, list):
        raise ValueError("task file must contain a list or top-level tasks list")
    return {str(task.get("task_id")): task for task in raw_tasks if isinstance(task, dict)}


def evaluate_run(run_dir: Path, task_file: Path | None = None) -> dict[str, Any]:
    summary = load_json(run_dir / "run_summary.json", {})
    if not isinstance(summary, dict):
        summary = {}
    task = _resolve_task(summary, task_file)
    answer = str(summary.get("final_answer") or "")
    criteria = task.get("success_criteria") if isinstance(task, dict) else {}
    criteria = criteria if isinstance(criteria, dict) else {}
    task_id = str(task.get("task_id") or _infer_task_id(summary) or "")

    if task_id.startswith("csrankings_") or str(task.get("site") or "").startswith("csrankings"):
        result = _evaluate_csrankings(task_id, answer, criteria)
    else:
        result = _evaluate_open_task(task_id, answer, summary, criteria)

    result.update(
        {
            "task_id": task_id,
            "run_id": summary.get("run_id", run_dir.name),
            "trace_dir": str(run_dir),
            "harness_success": bool(summary.get("success")),
            "final_status": summary.get("final_status", ""),
            "final_reason": summary.get("final_reason", ""),
        }
    )
    return result


def _resolve_task(summary: dict[str, Any], task_file: Path | None) -> dict[str, Any]:
    metadata = summary.get("task_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    task_id = str(metadata.get("task_id") or _infer_task_id(summary) or "")
    if task_file and task_file.exists():
        tasks = load_task_definitions(task_file)
        if task_id in tasks:
            return tasks[task_id]
        instruction = _normalize_text(str(summary.get("instruction") or ""))
        for task in tasks.values():
            task_instruction = _normalize_text(str(task.get("instruction") or ""))
            if instruction and task_instruction and instruction == task_instruction:
                return task
    return metadata


def _infer_task_id(summary: dict[str, Any]) -> str:
    metadata = summary.get("task_metadata")
    if isinstance(metadata, dict) and metadata.get("task_id"):
        return str(metadata["task_id"])
    run_id = str(summary.get("run_id") or "")
    match = re.match(r"(.+)_\d{3}$", run_id)
    return match.group(1) if match else ""


def _evaluate_csrankings(task_id: str, answer: str, criteria: dict[str, Any]) -> dict[str, Any]:
    if task_id == "csrankings_us_nlp_2020_2025_top_professor":
        return _result(
            "deterministic_csrankings_easy",
            [_check_contains(answer, "professor_name", "Graham Neubig")],
        )

    if task_id == "csrankings_cn_ml_2015_2025_top_scholar_profile":
        expected = criteria.get("expected_answer") if isinstance(criteria.get("expected_answer"), dict) else {}
        checks = [
            _check_any(answer, "scholar_name", ["\u6731\u519b", "Jun Zhu"]),
            _check_any(answer, "university", ["\u6e05\u534e\u5927\u5b66", "\u6e05\u534e", "Tsinghua"]),
            _check_any(answer, "position", ["\u6559\u6388", "Professor"]),
            _check_contains(answer, "email", str(expected.get("email") or "dcszj@mail.tsinghua.edu.cn")),
            _check_citation_count(answer),
            _check_contains(answer, "h_index", str(expected.get("h_index") or "105")),
            _check_contains(
                answer,
                "top_cited_paper_title",
                str(expected.get("top_cited_paper_title") or "Grounding dino: Marrying dino with grounded pre-training for open-set object detection"),
            ),
        ]
        return _result("deterministic_csrankings_medium", checks)

    if task_id == "csrankings_us_ai_systems_balanced_school_faculty":
        checks = [
            _check_any(answer, "school", ["Carnegie Mellon University", "CMU"]),
            _check_contains(answer, "faculty", "Zhihao Jia"),
            _check_contains(answer, "median_rank", "1.5"),
            _check_contains(answer, "average_rank", "2.375"),
            _check_labeled_number(answer, "worst_rank", ["\u6700\u5dee\u6392\u540d", "worst"], 6),
            _check_labeled_number(answer, "covered_area_count", ["\u8986\u76d6\u65b9\u5411\u6570", "covered"], 7),
            _check_labeled_number(answer, "total_pubs", ["# Pubs \u603b\u548c", "Pubs \u603b\u548c", "total"], 28),
            _check_number_sequence(answer, "school_ranks", [1, 1, 1, 1, 4, 3, 6, 2]),
            _check_number_sequence(answer, "faculty_pubs", [1, 8, 2, 0, 6, 3, 2, 6]),
        ]
        return _result("deterministic_csrankings_hard", checks)

    expected = criteria.get("expected_answer") if isinstance(criteria.get("expected_answer"), dict) else {}
    checks = [_check_contains(answer, key, value) for key, value in _flatten_expected(expected).items()]
    return _result("deterministic_structured_contains", checks)


def _evaluate_open_task(
    task_id: str,
    answer: str,
    summary: dict[str, Any],
    criteria: dict[str, Any],
) -> dict[str, Any]:
    evidence_text = _evidence_text(answer, summary)
    checks = _known_open_task_checks(task_id, answer, evidence_text)
    if checks is None:
        checks = _generic_open_task_checks(answer, evidence_text, criteria)

    score = _score(checks)
    return {
        "evaluator": "open_task_field_coverage",
        "success": None,
        "needs_review": True,
        "score": score,
        "checks": checks,
        "reason": (
            "Open-ended task: field/source coverage was checked automatically, "
            "but final success should be decided by human review or an LLM judge."
        ),
        "answer_length": len(answer),
        "harness_success": bool(summary.get("success")),
    }


def _known_open_task_checks(task_id: str, answer: str, evidence_text: str) -> list[dict[str, Any]] | None:
    if task_id == "bilibili_public_video_intro":
        return [
            _check_semantic(answer, "video_title", ["\u89c6\u9891\u6807\u9898", "\u6807\u9898", "title"]),
            _check_semantic(answer, "video_link", ["\u89c6\u9891\u94fe\u63a5", "\u94fe\u63a5", "url", "http"]),
            _check_semantic(
                answer,
                "visible_intro_or_absent_notice",
                ["\u7b80\u4ecb", "\u672a\u663e\u793a\u89c6\u9891\u7b80\u4ecb", "description", "intro"],
            ),
            _check_min_source_urls(evidence_text, "bilibili.com", 1),
        ]

    if task_id == "bilibili_ai_search_top3_audience":
        return [
            _check_semantic(answer, "video_title", ["\u89c6\u9891\u6807\u9898", "\u6807\u9898", "title"]),
            _check_semantic(answer, "audience", ["\u53d7\u4f17", "\u9002\u5408", "\u9762\u5411", "audience"]),
            _check_semantic(answer, "reason", ["\u7406\u7531", "\u56e0\u4e3a", "\u4f9d\u636e", "reason"]),
            _check_item_count(answer, "at_least_3_video_items", 3),
            _check_min_source_urls(evidence_text, "bilibili.com", 1),
        ]

    if task_id == "bilibili_bangumi_recommend_html":
        return [
            _check_semantic(answer, "bangumi_name", ["\u756a\u5267\u540d\u79f0", "\u540d\u79f0", "title"]),
            _check_semantic(answer, "bilibili_score", ["\u8bc4\u5206", "\u8bc4\u5206\u4eba\u6570", "score", "rating"]),
            _check_semantic(answer, "intro", ["\u7b80\u4ecb", "\u6458\u8981", "summary", "synopsis"]),
            _check_semantic(answer, "premiere_year", ["\u9996\u64ad\u5e74\u4efd", "\u5e74\u4efd", "year"]),
            _check_semantic(answer, "genre_or_tags", ["\u7c7b\u578b", "\u6807\u7b7e", "genre", "tag"]),
            _check_semantic(answer, "studio", ["\u5236\u4f5c\u516c\u53f8", "\u5236\u4f5c", "studio"]),
            _check_semantic(answer, "staff", ["\u4e3b\u521b", "\u5bfc\u6f14", "\u7cfb\u5217\u6784\u6210", "\u539f\u4f5c", "director", "staff"]),
            _check_semantic(
                answer,
                "recommendation_reason",
                ["\u63a8\u8350\u7406\u7531", "\u7406\u7531", "\u4e3a\u4ec0\u4e48", "\u503c\u5f97\u63a8\u8350", "recommend"],
            ),
            _check_semantic(answer, "download_link", ["\u4e0b\u8f7d\u94fe\u63a5", "download", "peanutdl"]),
            _check_semantic(answer, "html_artifact", ["html", "<html", "<!doctype"]),
            _check_min_source_urls(evidence_text, "bilibili.com", 5),
        ]

    if task_id == "guangzhou_ctrip_top_attraction":
        return [
            _check_semantic(answer, "attraction_name", ["\u666f\u70b9\u540d\u79f0", "\u666f\u70b9", "\u540d\u79f0"]),
            _check_semantic(answer, "ctrip_rating", ["\u643a\u7a0b\u8bc4\u5206", "\u8bc4\u5206", "rating"]),
            _check_semantic(answer, "area_or_address", ["\u533a\u57df", "\u5730\u5740", "address", "area"]),
            _check_semantic(answer, "opening_or_ticket", ["\u5f00\u653e\u65f6\u95f4", "\u95e8\u7968", "\u7968\u52a1", "open", "ticket"]),
            _check_min_source_urls(evidence_text, "ctrip.com", 1),
        ]

    if task_id == "guangzhou_ctrip_chimelong_sights_medium":
        return [
            _check_semantic(answer, "attraction_name", ["\u666f\u70b9\u540d\u79f0", "\u666f\u70b9", "\u540d\u79f0"]),
            _check_semantic(answer, "rating_or_heat", ["\u643a\u7a0b\u8bc4\u5206", "\u8bc4\u5206", "\u70ed\u5ea6", "rating"]),
            _check_semantic(answer, "area_or_address", ["\u5730\u5740", "\u533a\u57df", "address", "area"]),
            _check_semantic(answer, "opening_hours", ["\u5f00\u653e\u65f6\u95f4", "\u5f00\u56ed", "open"]),
            _check_semantic(answer, "ticket_info", ["\u95e8\u7968", "\u7968\u52a1", "\u4ef7\u683c", "ticket"]),
            _check_semantic(answer, "recommend_one", ["\u63a8\u8350", "\u9996\u9009", "\u6700\u503c\u5f97", "recommend"]),
            _check_min_source_urls(evidence_text, "you.ctrip.com/sight", 3),
        ]

    if task_id == "guangzhou_two_day_real_web_plan":
        return [
            _check_semantic(answer, "two_day_itinerary", ["\u4e24\u65e5\u884c\u7a0b", "\u7b2c\u4e00\u5929", "\u7b2c\u4e8c\u5929", "day 1", "day 2"]),
            _check_semantic(answer, "time_slots", ["\u4e0a\u5348", "\u4e0b\u5348", "\u665a\u4e0a", "morning", "afternoon", "evening"]),
            _check_semantic(answer, "hotel_comparison", ["\u9152\u5e97", "\u4f4f\u5bbf", "\u63a8\u8350", "hotel"]),
            _check_semantic(answer, "restaurant_info", ["\u9910\u5385", "\u4eba\u5747", "\u65e9\u8336", "\u5c0f\u5403", "restaurant"]),
            _check_semantic(answer, "attraction_official_info", ["\u666f\u70b9", "\u5f00\u653e\u65f6\u95f4", "\u9884\u7ea6", "\u95e8\u7968", "ticket"]),
            _check_semantic(answer, "transport", ["\u4ea4\u901a", "\u5730\u94c1", "\u516c\u4ea4", "metro", "transport"]),
            _check_semantic(answer, "budget", ["\u9884\u7b97", "\u4ef7\u683c", "\u4eba\u5747", "budget"]),
            _check_semantic(answer, "rain_plan", ["\u96e8\u5929", "\u66ff\u6362", "rain"]),
            _check_min_source_urls(evidence_text, "", 9),
        ]

    return None


def _generic_open_task_checks(answer: str, evidence_text: str, criteria: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    required_fields = [str(item) for item in criteria.get("required_fields") or [] if str(item).strip()]
    source_domains = [str(item) for item in criteria.get("required_sources") or [] if str(item).strip()]
    for field in required_fields:
        checks.append(_check_semantic_field(answer, field))
    for domain in source_domains:
        checks.append(_check_contains(evidence_text, f"source:{domain}", domain))
    if criteria.get("required_detail_pages"):
        checks.append(_check_min_source_urls(evidence_text, str(source_domains[0]) if source_domains else "", int(criteria.get("required_detail_pages") or 0)))
    return checks


def _flatten_expected(payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten_expected(value, name))
        else:
            flat[name] = value
    return flat


def _result(evaluator: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    score = _score(checks)
    missing = [check["name"] for check in checks if not check["passed"]]
    return {
        "evaluator": evaluator,
        "success": bool(checks) and not missing,
        "needs_review": False,
        "score": score,
        "checks": checks,
        "reason": "all deterministic checks passed" if not missing else "missing or mismatched: " + ", ".join(missing),
    }


def _score(checks: list[dict[str, Any]]) -> float:
    if not checks:
        return 0.0
    return round(sum(1 for check in checks if check.get("passed")) / len(checks), 4)


def _check_contains(answer: str, name: str, expected: Any) -> dict[str, Any]:
    expected_text = str(expected)
    normalized_answer = _normalize_text(answer)
    passed = any(_normalize_text(value) in normalized_answer for value in _text_variants(expected_text))
    return {"name": name, "passed": passed, "expected": expected_text}


def _check_any(answer: str, name: str, expected_values: list[str]) -> dict[str, Any]:
    normalized = _normalize_text(answer)
    passed = any(_normalize_text(variant) in normalized for value in expected_values for variant in _text_variants(value))
    return {"name": name, "passed": passed, "expected_any": expected_values}


def _check_semantic(answer: str, name: str, terms: list[str]) -> dict[str, Any]:
    return _check_any(answer, name, terms)


def _check_citation_count(answer: str) -> dict[str, Any]:
    normalized = _normalize_text(answer)
    patterns = ["6.8w", "6.8\u4e07", "68000", "68,000", "68000+"]
    passed = any(_normalize_text(pattern) in normalized for pattern in patterns)
    if not passed:
        for match in re.findall("(\\d+(?:\\.\\d+)?)\\s*(?:w|\u4e07)", answer, flags=re.IGNORECASE):
            try:
                value = float(match)
            except ValueError:
                continue
            if 6.7 <= value <= 6.9:
                passed = True
                break
    return {"name": "google_scholar_citations_rounded", "passed": passed, "expected": "around 6.8w"}


def _check_labeled_number(answer: str, name: str, labels: list[str], expected: int | float) -> dict[str, Any]:
    normalized_answer = _normalize_text(answer)
    expected_text = _normalize_number(expected)
    passed = False
    for label in labels:
        for label_variant in _text_variants(label):
            label_norm = _normalize_text(label_variant)
            index = normalized_answer.find(label_norm)
            if index < 0:
                continue
            window = normalized_answer[index : index + 90]
            if expected_text in [_normalize_number(match) for match in re.findall(r"\d+(?:\.\d+)?", window)]:
                passed = True
                break
        if passed:
            break
    if not passed:
        passed = expected_text in [_normalize_number(match) for match in re.findall(r"\d+(?:\.\d+)?", normalized_answer)]
    return {"name": name, "passed": passed, "expected": expected}


def _check_number_sequence(answer: str, name: str, expected: list[int]) -> dict[str, Any]:
    compact = _normalize_text(answer)
    sequence_patterns = [
        "".join(str(item) for item in expected),
        ",".join(str(item) for item in expected),
        "\u3001".join(str(item) for item in expected),
        " ".join(str(item) for item in expected),
    ]
    passed = any(_normalize_text(pattern) in compact for pattern in sequence_patterns)
    if not passed:
        numbers = [int(match) for match in re.findall(r"\d+", answer)]
        expected_len = len(expected)
        passed = any(numbers[index : index + expected_len] == expected for index in range(max(0, len(numbers) - expected_len + 1)))
    return {"name": name, "passed": passed, "expected": expected}


def _check_semantic_field(answer: str, field: str) -> dict[str, Any]:
    aliases = {
        "\u89c6\u9891\u6807\u9898": ["\u89c6\u9891\u6807\u9898", "\u6807\u9898", "title"],
        "\u89c6\u9891\u94fe\u63a5": ["\u89c6\u9891\u94fe\u63a5", "\u94fe\u63a5", "url", "http"],
        "\u53d7\u4f17": ["\u53d7\u4f17", "\u9002\u5408", "\u9762\u5411", "audience"],
        "\u7406\u7531": ["\u7406\u7531", "\u56e0\u4e3a", "\u4f9d\u636e", "reason"],
        "\u666f\u70b9\u540d\u79f0": ["\u666f\u70b9\u540d\u79f0", "\u666f\u70b9", "\u540d\u79f0"],
        "\u643a\u7a0b\u8bc4\u5206": ["\u643a\u7a0b\u8bc4\u5206", "\u8bc4\u5206", "rating"],
        "\u643a\u7a0b\u8bc4\u5206\u6216\u70ed\u5ea6": ["\u643a\u7a0b\u8bc4\u5206", "\u8bc4\u5206", "\u70ed\u5ea6", "rating"],
        "\u5730\u5740\u6216\u6240\u5728\u533a\u57df": ["\u5730\u5740", "\u533a\u57df", "\u6240\u5728", "address", "area"],
        "\u533a\u57df\u6216\u5730\u5740": ["\u5730\u5740", "\u533a\u57df", "\u6240\u5728", "address", "area"],
        "\u5f00\u653e\u65f6\u95f4": ["\u5f00\u653e\u65f6\u95f4", "\u5f00\u56ed", "\u8425\u4e1a\u65f6\u95f4", "open"],
        "\u5f00\u653e\u65f6\u95f4\u6216\u95e8\u7968\u63d0\u793a": ["\u5f00\u653e\u65f6\u95f4", "\u95e8\u7968", "\u7968\u52a1", "open", "ticket"],
        "\u95e8\u7968\u4ef7\u683c\u6216\u7968\u52a1\u63d0\u793a": ["\u95e8\u7968", "\u7968\u52a1", "\u4ef7\u683c", "ticket"],
        "\u63a8\u8350 1 \u4e2a\u666f\u70b9": ["\u63a8\u8350", "\u9996\u9009", "\u6700\u503c\u5f97", "recommend"],
        "\u6765\u6e90 URL": ["http", "\u6765\u6e90", "url"],
    }
    terms = aliases.get(field, [field])
    return _check_any(answer, f"field:{field}", terms)


def _check_item_count(answer: str, name: str, minimum: int) -> dict[str, Any]:
    numbered_items = re.findall(r"(?:^|\n)\s*(?:\d+[\.\)]|[-*])\s+", answer)
    urls = re.findall(r"https?://[^\s<>)\]}\"']+", answer)
    title_markers = sum(
        _normalize_text(marker) in _normalize_text(answer)
        for marker in ["\u6807\u9898", "title", "\u89c6\u9891"]
    )
    actual = max(len(numbered_items), len(set(urls)), title_markers)
    return {"name": name, "passed": actual >= minimum, "expected": minimum, "actual": actual}


def _check_min_source_urls(answer: str, domain_fragment: str, minimum: int) -> dict[str, Any]:
    urls = re.findall(r"https?://[^\s<>)\]}\"']+", answer)
    if domain_fragment:
        urls = [url for url in urls if domain_fragment in url]
    distinct_urls = sorted(set(url.rstrip(".,;\uff0c\u3002\uff1b") for url in urls))
    return {
        "name": f"min_source_urls:{domain_fragment or 'any'}",
        "passed": len(distinct_urls) >= minimum,
        "expected": minimum,
        "actual": len(distinct_urls),
        "urls": distinct_urls[:10],
    }


def _evidence_text(answer: str, summary: dict[str, Any]) -> str:
    chunks = [answer, str(summary.get("final_url") or "")]
    checklist = summary.get("checklist")
    if checklist:
        chunks.append(json.dumps(checklist, ensure_ascii=False))
    return "\n".join(chunks)


def _text_variants(text: Any) -> set[str]:
    value = str(text or "")
    variants = {value}
    for decoder in ("gbk", "cp936", "latin1"):
        try:
            variants.add(value.encode("utf-8").decode(decoder))
        except UnicodeError:
            pass
    return {variant for variant in variants if variant}


def _normalize_text(text: Any) -> str:
    value = str(text or "").lower()
    return "".join(char for char in value if char.isalnum())


def _normalize_number(value: Any) -> str:
    text = str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
