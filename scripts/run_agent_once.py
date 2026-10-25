from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.prompts import build_react_messages, messages_to_prompt_text  # noqa: E402
from agents.react_agent import SingleStepReactAgent, safe_wait_action  # noqa: E402
from browser.playwright_env import PlaywrightEnv  # noqa: E402
from eval.runner import write_json  # noqa: E402
from models.base_client import ModelClientError, load_env_file  # noqa: E402
from models.deepseek_client import DeepSeekClient  # noqa: E402
from models.openai_compatible_client import OpenAICompatibleClient  # noqa: E402
from perception.som_marker import draw_set_of_marks  # noqa: E402


TRACE_DIR = Path("traces/agent_once")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one model-decided Web-VLMAgent action.")
    parser.add_argument("--url", required=True, help="Start URL to open in Chromium.")
    parser.add_argument("--task", required=True, help="Natural language task instruction.")
    parser.add_argument("--provider", choices=["deepseek", "openai"], default="deepseek")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file()
    TRACE_DIR.mkdir(parents=True, exist_ok=True)

    screenshot_path = TRACE_DIR / "screenshot.png"
    candidates_path = TRACE_DIR / "candidates.json"
    marked_screenshot_path = TRACE_DIR / "marked_screenshot.png"
    accessibility_tree_path = TRACE_DIR / "accessibility_tree.txt"
    page_text_path = TRACE_DIR / "page_text.txt"
    prompt_path = TRACE_DIR / "prompt.txt"
    raw_response_path = TRACE_DIR / "raw_response.txt"
    parsed_action_path = TRACE_DIR / "parsed_action.json"
    execution_result_path = TRACE_DIR / "execution_result.json"

    try:
        with PlaywrightEnv() as env:
            env.open_url(args.url)
            env.screenshot(screenshot_path)
            accessibility_tree_text = env.accessibility_snapshot()
            _write_text(accessibility_tree_path, accessibility_tree_text)
            candidates = env.candidates()
            page_text = _page_text(env, candidates)
            _write_text(page_text_path, page_text)
            write_json(candidates_path, [candidate.model_dump() for candidate in candidates])
            draw_set_of_marks(
                screenshot_path=screenshot_path,
                candidates=candidates,
                output_path=marked_screenshot_path,
            )

            current_url = env.page.url if env.page is not None else args.url
            messages = build_react_messages(
                task=args.task,
                current_url=current_url,
                candidates=candidates,
                action_history=[],
                marked_screenshot_path=marked_screenshot_path,
                accessibility_tree_text=accessibility_tree_text,
                page_text=page_text,
                include_screenshot_image=args.provider == "openai",
            )
            _write_text(prompt_path, messages_to_prompt_text(messages))

            try:
                client = _create_client(args.provider)
                agent = SingleStepReactAgent(client)
                agent_result = agent.decide(messages)
            except ModelClientError as exc:
                _write_text(raw_response_path, "")
                fallback_action = safe_wait_action(str(exc))
                write_json(parsed_action_path, fallback_action.model_dump())
                execution_result = env.execute_action(fallback_action.to_browser_action(), candidates)
                execution_result.update(
                    {
                        "ok": False,
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                        "fallback_action_used": True,
                        "model_provider": args.provider,
                    }
                )
                write_json(execution_result_path, execution_result)
                _print_summary(False, len(candidates), parsed_action_path, execution_result)
                return 1

            _write_text(raw_response_path, _format_raw_responses(agent_result.raw_responses))
            write_json(parsed_action_path, agent_result.parsed_action.model_dump())

            execution_result = env.execute_action(agent_result.parsed_action.to_browser_action(), candidates)
            execution_result.update(
                {
                    "fallback_action_used": agent_result.fallback_used,
                    "model_parse_error": agent_result.parse_error,
                    "model_provider": args.provider,
                }
            )
            if agent_result.fallback_used:
                execution_result.update(
                    {
                        "ok": False,
                        "error_type": "model_output_error",
                        "error": agent_result.parse_error,
                    }
                )
            write_json(execution_result_path, execution_result)
            _print_summary(bool(execution_result["ok"]), len(candidates), parsed_action_path, execution_result)
            return 0 if execution_result["ok"] else 1
    except Exception as exc:
        execution_result = {
            "ok": False,
            "action": None,
            "url_before": args.url,
            "url_after": None,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "model_provider": args.provider,
        }
        write_json(execution_result_path, execution_result)
        print(f"Trace directory: {TRACE_DIR}")
        print(f"Execution ok: False")
        print(f"Error type: {execution_result['error_type']}")
        print(f"Error: {execution_result['error']}")
        return 1


def _create_client(provider: str) -> OpenAICompatibleClient:
    if provider == "deepseek":
        return DeepSeekClient.from_env()
    return OpenAICompatibleClient.from_env()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _format_raw_responses(raw_responses: list[str]) -> str:
    if not raw_responses:
        return ""
    chunks = []
    for index, raw_response in enumerate(raw_responses, start=1):
        chunks.append(f"--- attempt {index} ---\n{raw_response}")
    return "\n\n".join(chunks) + "\n"


def _page_text(env: PlaywrightEnv, candidates: list[object]) -> str:
    candidate_text = "\n".join(getattr(candidate, "text", "") for candidate in candidates if getattr(candidate, "text", ""))
    if env.page is None:
        return candidate_text
    try:
        body_text = env.page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        body_text = ""
    return f"{body_text}\n{candidate_text}".strip()


def _print_summary(
    ok: bool,
    candidate_count: int,
    parsed_action_path: Path,
    execution_result: dict[str, object],
) -> None:
    print(f"Trace directory: {TRACE_DIR}")
    print(f"Candidates: {candidate_count}")
    print(f"Parsed action: {parsed_action_path}")
    print(f"Execution ok: {ok}")
    if not ok:
        print(f"Error type: {execution_result.get('error_type')}")
        print(f"Error: {execution_result.get('error')}")


if __name__ == "__main__":
    raise SystemExit(main())
