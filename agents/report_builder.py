from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from models.base_client import BaseChatClient, ChatMessage

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REPORT_SYSTEM_PROMPT = """You are Web-VLMAgent's final report designer.

Generate a polished standalone HTML report from the agent's completed web task. The result should look like a premium public showcase page, closer to a high-end product/travel/editorial landing page than to a markdown report or admin dashboard.

Hard rules:
- Return only a complete HTML document. Do not wrap it in markdown.
- Use the user's language for visible content.
- Use only facts present in the provided final answer, visited URLs, metadata, and screenshot asset list. Do not invent new facts, prices, ratings, names, dates, or source URLs.
- Use provided screenshot asset URLs generously as the page's main visual material. Prefer raw_screenshot_url for beautiful visuals; use marked_screenshot_url in evidence/trajectory sections where labels help. These URLs are local relative paths; copy them exactly and do not rewrite them into /api/file URLs.
- When 5 or more screenshot assets are available, include at least 5 distinct images in the report. When fewer are available, use all useful images. Do not leave the report text-heavy if visual assets exist.
- Keep the HTML complete but reasonably compact, ideally 18k-35k characters. Use concise editorial sections instead of copying the entire final answer verbatim.
- Do not use external image URLs, external CSS, external fonts, JavaScript, forms, login widgets, tracking pixels, iframes, or embedded third-party content.
- Keep all CSS inline inside a <style> tag.
- Make source links clickable and clearly labeled.
- If some requested information was blocked, unclear, or incomplete, present that limitation honestly in a polished caveat section; the overall page should still feel like a finished report.
- Do not use marketing claims such as "deeply verified", "all data confirmed", "best", "Michelin", "Meituan", "Dianping", or any platform/source claim unless the final answer or visited URL list explicitly supports it.
- Do not use emoji anywhere in visible report text, even if the final answer contains emoji. Convert them into plain, elegant text labels.
- Prefer a premium neutral palette: white, near-white, charcoal, cool gray, and one restrained accent color. Avoid beige/tan backgrounds, loud gradients, and one-hue flat themes.

Design requirements:
- The first viewport must be a cinematic hero: one provided raw screenshot used prominently as a full-bleed or oversized visual, with a concise title, subtitle, and 3-4 refined highlight chips over/near it.
- After the hero, create a visual narrative with multiple large image modules, side-by-side screenshot/text sections, timeline/route cards, comparison/detail cards, budget/checklist/caveat blocks, and a source wall when they fit the task.
- Include a curated evidence gallery near the end that uses several screenshots with short captions explaining what each page contributed.
- Summarize long source facts into polished cards, route modules, and comparison sections. Preserve all important facts and URLs, but avoid raw markdown dumps.
- Use generous whitespace, premium typography with system fonts, restrained colors, subtle shadows, thin borders, rounded cards, and responsive layouts.
- Make screenshots look intentional: use object-fit crops, captions, and a gallery. Do not let Set-of-Marks labels dominate unless the section is explicitly about evidence.
- Keep the page elegant and presentation-ready on a 1280px desktop viewport and readable on mobile.
- Avoid one-hue flat palettes, raw bullet dumps, tiny dense text, and generic report tables as the main visual structure.
"""


REPORT_PLAN_PROMPT = """You are Web-VLMAgent's report architect.

Create a compact, task-agnostic plan for a premium standalone HTML report. The report may be about travel, academic research, shopping comparison, policy lookup, documentation research, or any other web task.

Hard rules:
- Return only JSON. No markdown fences.
- Use the user's language for visible text.
- Do not invent facts. Use only the final answer, visited URLs, metadata, and screenshot assets.
- The plan must be generic and based on the user's task, not on hard-coded domains.
- Keep section count between 5 and 8.
- Each section should have an id, title, role, and brief instructions for what that section should contain.
- Include visual_asset_steps when screenshots should be used in that section.

JSON schema:
{
  "title": "short report title",
  "subtitle": "one sentence subtitle",
  "chips": ["3-4 concise highlight chips"],
  "theme": {
    "accent": "#hex",
    "mood": "brief visual direction"
  },
  "sections": [
    {
      "id": "safe-kebab-id",
      "title": "section title",
      "role": "hero|overview|comparison|timeline|details|evidence|sources|caveats|summary",
      "instructions": "what this section should present",
      "visual_asset_steps": ["step_000"]
    }
  ]
}
"""


REPORT_SECTION_PROMPT = """You are Web-VLMAgent's section-level HTML designer.

Generate one polished HTML fragment for a single section of a standalone report. This is one part of a larger page; another process will assemble the final document.

Hard rules:
- Return only an HTML fragment. Do not return <!doctype>, <html>, <head>, <body>, <style>, <script>, markdown fences, forms, iframes, or external resources.
- Use the user's language for visible text.
- Use only facts present in the provided final answer, visited URLs, metadata, and screenshot assets. Do not invent facts, prices, ratings, names, dates, or source URLs.
- Keep the fragment compact, ideally 2k-6k characters.
- Use semantic tags and reusable classes such as report-section, section-grid, feature-card, image-card, source-wall, route-card, evidence-card, caveat-card.
- Include provided screenshot asset URLs where useful. Prefer raw_screenshot_url for editorial visuals and marked_screenshot_url for evidence. These URLs are local relative paths; copy them exactly and do not rewrite them into /api/file URLs.
- Make links clickable with <a href="...">.
- Preserve important caveats and limitations if the final answer contains them.
"""


PARTIAL_ANSWER_SYSTEM_PROMPT = """You are Web-VLMAgent's graceful failure summarizer.

The agent could not fully complete the user's web task. Generate a useful partial final answer from the trace evidence gathered so far.

Hard rules:
- Use the user's language.
- Do not pretend the task succeeded.
- Use only facts present in the provided trace evidence, visited URLs, and metadata. Do not invent prices, ratings, schedules, hotel details, restaurant details, or source URLs.
- Clearly separate: what was completed, what reliable information was found, what remains incomplete, why the run failed, and source URLs.
- Start with a short status line that says this is a partial/incomplete result, not a successful completion.
- Do not claim the agent opened or verified any platform unless it appears in visited_urls or trace_evidence URLs.
- Do not upgrade weak evidence into confident recommendations. If evidence is insufficient for a requested item, say that it was not confirmed from opened pages.
- Do not use marketing language. Be clear, calm, and useful.
- The answer should be useful enough to display in a final HTML report.
"""


def generate_final_html_report(
    client: BaseChatClient,
    run_dir: Path,
    instruction: str,
    final_answer: str,
    summary: dict[str, Any],
    max_assets: int = 10,
) -> str:
    """Generate the final display HTML through small model-authored sections."""
    assets = _collect_report_assets(run_dir, max_assets=max_assets)
    visited_urls = _collect_visited_urls(run_dir, summary)
    metadata = {
        "run_id": summary.get("run_id"),
        "status": summary.get("final_status"),
        "success": summary.get("success"),
        "steps_used": summary.get("steps_used"),
        "provider": summary.get("provider"),
        "model": summary.get("model"),
        "final_url": summary.get("final_url"),
        "duration_seconds": summary.get("duration_seconds"),
    }
    report_context = {
        "user_task": instruction,
        "agent_final_answer": final_answer,
        "run_metadata": metadata,
        "visited_urls": visited_urls,
        "screenshot_assets": assets,
    }
    plan = _generate_report_plan(client, report_context)
    sections = _normalize_report_sections(plan, assets)
    fragments: list[str] = []
    for index, section in enumerate(sections, start=1):
        fragment = _generate_report_section(
            client=client,
            report_context=report_context,
            plan=plan,
            section=section,
            section_index=index,
            section_count=len(sections),
        )
        fragments.append(_sanitize_html_fragment(fragment))
    return _assemble_sectioned_report(plan=plan, fragments=fragments, assets=assets, visited_urls=visited_urls, metadata=metadata)


def _generate_report_plan(client: BaseChatClient, report_context: dict[str, Any], max_attempts: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None
    compact_context = _compact_report_context(report_context)
    for attempt in range(max_attempts):
        try:
            raw = client.complete(
                [
                    {"role": "system", "content": REPORT_PLAN_PROMPT},
                    {"role": "user", "content": json.dumps(compact_context, ensure_ascii=False, indent=2)},
                ]
            )
            plan = _extract_json_object(raw)
            if not isinstance(plan, dict):
                raise ValueError("report plan is not a JSON object")
            if not isinstance(plan.get("sections"), list) or not plan["sections"]:
                raise ValueError("report plan has no sections")
            return plan
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"report_plan_generation_failed: {last_error}")


def _generate_report_section(
    client: BaseChatClient,
    report_context: dict[str, Any],
    plan: dict[str, Any],
    section: dict[str, Any],
    section_index: int,
    section_count: int,
    max_attempts: int = 3,
) -> str:
    last_error: Exception | None = None
    section_context = {
        "report_title": plan.get("title"),
        "report_subtitle": plan.get("subtitle"),
        "report_chips": plan.get("chips"),
        "theme": plan.get("theme"),
        "section_index": section_index,
        "section_count": section_count,
        "section": section,
        "user_task": report_context.get("user_task"),
        "agent_final_answer": _clip_text(str(report_context.get("agent_final_answer") or ""), 9000),
        "run_metadata": report_context.get("run_metadata"),
        "visited_urls": report_context.get("visited_urls"),
        "screenshot_assets": _assets_for_section(section, report_context.get("screenshot_assets") or []),
    }
    for attempt in range(max_attempts):
        try:
            raw = client.complete(
                [
                    {"role": "system", "content": REPORT_SECTION_PROMPT},
                    {"role": "user", "content": json.dumps(section_context, ensure_ascii=False, indent=2)},
                ]
            )
            fragment = _extract_html_fragment(raw)
            if not fragment.strip():
                raise ValueError("empty section fragment")
            return fragment
        except Exception as exc:
            last_error = exc
    section_id = section.get("id") or f"section-{section_index}"
    raise RuntimeError(f"report_section_generation_failed[{section_id}]: {last_error}")


def sanitize_model_html_report(document: str) -> str:
    """Keep model-authored reports display-only and safe for local viewing."""
    text = document.strip()
    text = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<\s*(iframe|object|embed|form|input|textarea|select|button)\b[^>]*>.*?<\s*/\s*\1\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<\s*(iframe|object|embed|form|input|textarea|select|button)\b[^>]*?/?>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+on[a-zA-Z]+\s*=\s*(['\"]).*?\1", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\s+on[a-zA-Z]+\s*=\s*[^\s>]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"javascript\s*:", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<meta\b[^>]*http-equiv\s*=\s*(['\"]?)refresh\1[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<link\b[^>]*>", "", text, flags=re.IGNORECASE)

    if "<html" not in text.lower():
        text = _wrap_body_html(text)
    if "charset" not in text[:500].lower():
        text = re.sub(r"<head(\s[^>]*)?>", lambda m: f"{m.group(0)}\n<meta charset=\"utf-8\">", text, count=1, flags=re.IGNORECASE)
    return text


def _sanitize_html_fragment(fragment: str) -> str:
    text = _extract_html_fragment(fragment)
    text = re.sub(r"<!doctype[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(html|head|body)\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<style\b[^>]*>.*?</style\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    return sanitize_model_html_report(_wrap_body_html(text)).split("<body>", 1)[-1].rsplit("</body>", 1)[0].strip()


def _assemble_sectioned_report(
    plan: dict[str, Any],
    fragments: list[str],
    assets: list[dict[str, Any]],
    visited_urls: list[str],
    metadata: dict[str, Any],
) -> str:
    title = html.escape(str(plan.get("title") or "Web-VLMAgent Report"))
    subtitle = html.escape(str(plan.get("subtitle") or "A model-generated web research report."))
    chips = [str(chip) for chip in plan.get("chips") or [] if str(chip).strip()][:4]
    accent = str((plan.get("theme") or {}).get("accent") or "#2f7b72")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = "#2f7b72"
    hero_asset = _first_asset_url(assets)
    hero_style = f"background-image:linear-gradient(120deg,rgba(8,19,16,.84),rgba(47,123,114,.42)),url('{html.escape(hero_asset)}');" if hero_asset else ""
    chip_html = "".join(f"<span>{html.escape(chip)}</span>" for chip in chips)
    fragment_html = "\n".join(fragments)
    source_links = "".join(
        f'<a href="{html.escape(url)}" target="_blank" rel="noreferrer">{html.escape(url)}</a>'
        for url in visited_urls[:30]
    )
    meta_html = "".join(
        f"<span>{html.escape(label)}: <strong>{html.escape(value)}</strong></span>"
        for label, value in [
            ("Steps", str(metadata.get("steps_used") or "--")),
            ("Model", " / ".join(str(metadata.get(key) or "") for key in ("provider", "model")).strip(" /") or "--"),
            ("Status", str(metadata.get("status") or "--")),
        ]
    )
    return sanitize_model_html_report(f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --accent:{accent}; --ink:#101512; --muted:#67746d; --line:rgba(18,35,29,.13); --paper:#fbfcf8; --card:rgba(255,255,255,.88); }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:linear-gradient(180deg,#fbfcf8,#eef4ef); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; line-height:1.65; }}
a {{ color:#0f6c8e; text-decoration-thickness:1px; text-underline-offset:3px; overflow-wrap:anywhere; }}
.hero {{ min-height:66vh; display:grid; align-items:end; padding:72px min(7vw,96px); color:white; background:linear-gradient(120deg,#101f1b,var(--accent)); background-size:cover; background-position:center; {hero_style} }}
.hero-inner {{ width:min(1060px,100%); }}
.eyebrow {{ margin:0 0 16px; font-size:12px; letter-spacing:.18em; text-transform:uppercase; font-weight:850; opacity:.82; }}
h1 {{ margin:0; max-width:1000px; font-size:clamp(42px,7vw,92px); line-height:.96; letter-spacing:0; }}
.subtitle {{ margin:24px 0 0; max-width:780px; font-size:clamp(18px,2vw,24px); color:rgba(255,255,255,.84); }}
.chips,.meta-strip {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:28px; }}
.chips span,.meta-strip span {{ border:1px solid rgba(255,255,255,.26); border-radius:999px; padding:9px 13px; background:rgba(255,255,255,.13); backdrop-filter:blur(16px); font-weight:800; }}
.page {{ width:min(1180px,calc(100vw - 34px)); margin:0 auto; padding:64px 0 92px; }}
.report-section {{ margin-top:28px; padding:34px; border:1px solid var(--line); border-radius:32px; background:var(--card); box-shadow:0 24px 70px rgba(22,46,37,.1); overflow:hidden; }}
.report-section h2 {{ margin:0 0 18px; font-size:clamp(28px,4vw,48px); line-height:1.08; }}
.report-section h3 {{ margin:22px 0 10px; font-size:24px; }}
.section-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }}
.feature-card,.route-card,.evidence-card,.caveat-card,.image-card {{ border:1px solid var(--line); border-radius:24px; padding:22px; background:white; box-shadow:0 12px 34px rgba(30,52,44,.07); }}
.image-card {{ padding:0; overflow:hidden; }}
.image-card img,.evidence-card img {{ display:block; width:100%; aspect-ratio:16/10; object-fit:cover; background:#f4f6f2; }}
.image-card figcaption,.evidence-card figcaption {{ padding:14px 16px; color:var(--muted); font-weight:760; }}
.source-wall {{ display:grid; gap:10px; }}
.final-sources {{ margin-top:28px; padding:30px; border:1px solid var(--line); border-radius:28px; background:#fff; }}
.source-links {{ display:grid; gap:9px; margin-top:12px; }}
@media (max-width:860px) {{ .section-grid {{ grid-template-columns:1fr; }} .hero {{ min-height:auto; padding:48px 24px; }} .report-section {{ padding:24px; }} }}
</style>
</head>
<body>
<header class="hero">
  <div class="hero-inner">
    <p class="eyebrow">Web-VLMAgent Report</p>
    <h1>{title}</h1>
    <p class="subtitle">{subtitle}</p>
    <div class="chips">{chip_html}</div>
    <div class="meta-strip">{meta_html}</div>
  </div>
</header>
<main class="page">
{fragment_html}
  <section class="final-sources">
    <h2>Sources</h2>
    <div class="source-links">{source_links}</div>
  </section>
</main>
</body>
</html>""")


def _extract_html_document(raw_response: str) -> str:
    text = raw_response.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    doctype_index = text.lower().find("<!doctype")
    html_index = text.lower().find("<html")
    if doctype_index >= 0:
        text = text[doctype_index:]
    elif html_index >= 0:
        text = text[html_index:]
    return text.strip()


def _extract_html_fragment(raw_response: str) -> str:
    text = raw_response.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    body_match = re.search(r"<body\b[^>]*>(.*?)</body\s*>", text, flags=re.IGNORECASE | re.DOTALL)
    if body_match:
        return body_match.group(1).strip()
    return text.strip()


def _extract_json_object(raw_response: str) -> dict[str, Any]:
    text = raw_response.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")
    return payload


def _wrap_body_html(body: str) -> str:
    return f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>Web-VLMAgent Report</title>
</head>
<body>{body}</body>
</html>"""


def _compact_report_context(report_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_task": report_context.get("user_task"),
        "agent_final_answer": _clip_text(str(report_context.get("agent_final_answer") or ""), 11000),
        "run_metadata": report_context.get("run_metadata"),
        "visited_urls": report_context.get("visited_urls"),
        "screenshot_assets": report_context.get("screenshot_assets"),
        "output_expectation": (
            "Plan a premium report. If the report is too large for one response, split it into sections; "
            "the system will ask for and assemble each section separately."
        ),
    }


def _normalize_report_sections(plan: dict[str, Any], assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_sections = plan.get("sections")
    sections = [section for section in raw_sections if isinstance(section, dict)] if isinstance(raw_sections, list) else []
    normalized: list[dict[str, Any]] = []
    for index, section in enumerate(sections[:8], start=1):
        section_id = re.sub(r"[^a-z0-9-]+", "-", str(section.get("id") or f"section-{index}").lower()).strip("-")
        normalized.append(
            {
                "id": section_id or f"section-{index}",
                "title": str(section.get("title") or f"Section {index}"),
                "role": str(section.get("role") or "details"),
                "instructions": str(section.get("instructions") or ""),
                "visual_asset_steps": [str(item) for item in section.get("visual_asset_steps") or []],
            }
        )
    if len(normalized) >= 5:
        return normalized
    asset_steps = [str(asset.get("step") or "") for asset in assets if asset.get("step")]
    return [
        {"id": "overview", "title": "Overview", "role": "overview", "instructions": "Summarize the completed task, key outcome, and how to read the report.", "visual_asset_steps": asset_steps[:1]},
        {"id": "findings", "title": "Key Findings", "role": "details", "instructions": "Present the most important findings from the final answer as polished cards.", "visual_asset_steps": asset_steps[1:3]},
        {"id": "comparison", "title": "Comparison", "role": "comparison", "instructions": "Compare entities, options, sources, or evidence relevant to the user's task.", "visual_asset_steps": asset_steps[3:5]},
        {"id": "plan", "title": "Actionable Plan", "role": "timeline", "instructions": "Turn the result into a route, timeline, decision path, or structured next-step view if applicable.", "visual_asset_steps": asset_steps[5:7]},
        {"id": "evidence", "title": "Evidence", "role": "evidence", "instructions": "Show screenshots and source URLs as evidence for the report.", "visual_asset_steps": asset_steps[:6]},
    ]


def _assets_for_section(section: dict[str, Any], assets: list[dict[str, Any]], max_assets: int = 5) -> list[dict[str, Any]]:
    desired = {str(step) for step in section.get("visual_asset_steps") or []}
    selected = [asset for asset in assets if str(asset.get("step")) in desired]
    if not selected:
        role = str(section.get("role") or "")
        if role == "hero":
            selected = assets[-2:]
        elif role in {"evidence", "sources"}:
            selected = assets[:max_assets]
        else:
            selected = assets[:2]
    return selected[:max_assets]


def _first_asset_url(assets: list[dict[str, Any]]) -> str:
    for asset in reversed(assets):
        url = str(asset.get("raw_screenshot_url") or asset.get("marked_screenshot_url") or "")
        if url:
            return url
    return ""


def _clip_text(text: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]} ... [truncated {len(compact) - max_chars} chars]"


def _collect_report_assets(run_dir: Path, max_assets: int) -> list[dict[str, Any]]:
    step_dirs = sorted(path for path in run_dir.glob("step_*") if path.is_dir())
    if not step_dirs:
        return []
    selected = _spread_select(step_dirs, max_assets)
    assets: list[dict[str, Any]] = []
    for step_dir in selected:
        parsed_action = _load_json(step_dir / "parsed_action.json")
        execution = _load_json(step_dir / "execution_result.json")
        action_name = parsed_action.get("action") or execution.get("action", {}).get("action") or "observe"
        item: dict[str, Any] = {
            "step": step_dir.name,
            "action": action_name,
            "url": execution.get("url_after") or execution.get("url_before") or "",
        }
        screenshot = step_dir / "screenshot.png"
        marked = step_dir / "marked_screenshot.png"
        if screenshot.exists():
            item["raw_screenshot_url"] = _asset_url(screenshot, run_dir)
        if marked.exists():
            item["marked_screenshot_url"] = _asset_url(marked, run_dir)
        if "raw_screenshot_url" in item or "marked_screenshot_url" in item:
            assets.append(item)
    return assets


def _spread_select(items: list[Path], limit: int) -> list[Path]:
    if limit <= 0 or len(items) <= limit:
        return items
    if limit == 1:
        return [items[-1]]
    selected: list[Path] = []
    last_index = len(items) - 1
    for slot in range(limit):
        index = round(slot * last_index / (limit - 1))
        candidate = items[index]
        if candidate not in selected:
            selected.append(candidate)
    return selected


def _collect_visited_urls(run_dir: Path, summary: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("final_url", "start_url"):
        value = str(summary.get(key) or "").strip()
        if value:
            urls.append(value)
    for step_dir in sorted(path for path in run_dir.glob("step_*") if path.is_dir()):
        execution = _load_json(step_dir / "execution_result.json")
        for key in ("url_before", "url_after"):
            value = str(execution.get(key) or "").strip()
            if value:
                urls.append(value)
    deduped: list[str] = []
    for url in urls:
        if url.startswith(("http://", "https://", "file://")) and url not in deduped:
            deduped.append(url)
    return deduped[:40]


def generate_partial_final_answer(
    client: BaseChatClient,
    run_dir: Path,
    instruction: str,
    summary: dict[str, Any],
    blocked_status: str,
    detail: str,
    max_steps: int = 24,
) -> str:
    evidence = _collect_text_evidence(run_dir, max_steps=max_steps)
    visited_urls = _collect_visited_urls(run_dir, summary)
    payload = {
        "user_task": instruction,
        "failure_status": blocked_status,
        "failure_detail": detail,
        "run_metadata": {
            "run_id": summary.get("run_id"),
            "steps_used": summary.get("steps_used"),
            "provider": summary.get("provider"),
            "model": summary.get("model"),
            "final_url": summary.get("final_url"),
        },
        "visited_urls": visited_urls,
        "trace_evidence": evidence,
        "output_expectation": (
            "Write the best possible partial final answer. It must be clear that the task failed/incomplete, "
            "but still preserve every useful confirmed finding and source gathered before failure."
        ),
    }
    messages: list[ChatMessage] = [
        {"role": "system", "content": PARTIAL_ANSWER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]
    return client.complete(messages).strip()

def _collect_text_evidence(run_dir: Path, max_steps: int) -> list[dict[str, Any]]:
    step_dirs = sorted(path for path in run_dir.glob("step_*") if path.is_dir())
    selected = _spread_select(step_dirs, max_steps) if step_dirs else []
    evidence: list[dict[str, Any]] = []
    for step_dir in selected:
        parsed_action = _load_json(step_dir / "parsed_action.json")
        execution = _load_json(step_dir / "execution_result.json")
        progress = _load_json(step_dir / "progress_check.json")
        page_text = ""
        page_text_path = step_dir / "page_text.txt"
        if page_text_path.exists():
            page_text = _truncate_evidence_text(page_text_path.read_text(encoding="utf-8", errors="replace"), 1400)
        item = {
            "step": step_dir.name,
            "action": parsed_action.get("action"),
            "action_text": parsed_action.get("text") or "",
            "url_before": execution.get("url_before") or "",
            "url_after": execution.get("url_after") or "",
            "progress": progress.get("reason") or "",
            "page_text_excerpt": page_text,
        }
        if any(str(value).strip() for value in item.values()):
            evidence.append(item)
    return evidence


def _truncate_evidence_text(text: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]} ... [truncated {len(compact) - max_chars} chars]"


def _asset_url(path: Path, base_dir: Path) -> str:
    try:
        rel = path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        try:
            rel = path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
        except ValueError:
            rel = path.as_posix()
    return quote(rel, safe="/")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
