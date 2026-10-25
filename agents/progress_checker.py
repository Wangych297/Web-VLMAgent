from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, parse_qs


@dataclass
class ProgressChecker:
    stuck_threshold: int = 3
    repeated_action_threshold: int = 3

    def check(
        self,
        previous_step_info: dict[str, Any],
        current_step_info: dict[str, Any],
        action: dict[str, Any],
        execution_result: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        action_executed = bool(execution_result.get("ok") or execution_result.get("success"))
        url_changed = previous_step_info.get("url") != current_step_info.get("url")
        candidate_count_changed = previous_step_info.get("candidate_count") != current_step_info.get("candidate_count")
        candidate_text_changed = (
            previous_step_info.get("candidate_text_signature")
            != current_step_info.get("candidate_text_signature")
        )
        screenshot_hash_changed = previous_step_info.get("screenshot_hash") != current_step_info.get("screenshot_hash")
        repeated_action = self._is_repeated_action(action, history)
        action_loop = self._is_action_loop(action, execution_result, history)
        submit_without_navigation = self._submit_without_navigation(action, execution_result)
        navigation_landed_elsewhere = self._navigation_landed_elsewhere(action, execution_result)
        same_page_after_action = (
            not url_changed
            and not candidate_count_changed
            and not candidate_text_changed
            and not screenshot_hash_changed
        )
        if self._is_scroll_action(action) and not same_page_after_action:
            repeated_action = False

        made_progress = action_executed and (
            url_changed
            or candidate_count_changed
            or candidate_text_changed
            or screenshot_hash_changed
        )
        if submit_without_navigation or action_loop or navigation_landed_elsewhere:
            made_progress = False
        no_change_count = self._no_change_count(history, same_page_after_action)
        needs_user_help = submit_without_navigation
        stuck = (
            needs_user_help
            or repeated_action
            or action_loop
            or navigation_landed_elsewhere
            or no_change_count >= self.stuck_threshold
        )

        reason = self._reason(
            action_executed=action_executed,
            made_progress=made_progress,
            repeated_action=repeated_action,
            action_loop=action_loop,
            same_page_after_action=same_page_after_action,
            no_change_count=no_change_count,
            submit_without_navigation=submit_without_navigation,
            navigation_landed_elsewhere=navigation_landed_elsewhere,
        )

        return {
            "made_progress": made_progress,
            "stuck": stuck,
            "needs_user_help": needs_user_help,
            "reason": reason,
            "signals": {
                "url_changed": url_changed,
                "candidate_count_changed": candidate_count_changed,
                "candidate_text_changed": candidate_text_changed,
                "screenshot_hash_changed": screenshot_hash_changed,
                "action_executed": action_executed,
                "repeated_action": repeated_action,
                "action_loop": action_loop,
                "same_page_after_action": same_page_after_action,
                "submit_without_navigation": submit_without_navigation,
                "navigation_landed_elsewhere": navigation_landed_elsewhere,
            },
            "no_change_count": no_change_count,
        }

    def _is_repeated_action(self, action: dict[str, Any], history: list[dict[str, Any]]) -> bool:
        signature = self._action_signature(action)
        repeats = 1

        for item in reversed(history):
            previous_signature = self._action_signature(item.get("action", {}))
            if previous_signature != signature:
                break
            repeats += 1

        return repeats >= self.repeated_action_threshold

    def _is_scroll_action(self, action: dict[str, Any]) -> bool:
        return str(action.get("action") or "").lower() == "scroll"

    def _is_action_loop(
        self,
        action: dict[str, Any],
        execution_result: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> bool:
        name = str(action.get("action") or "").lower()
        if name not in {"navigate", "search", "type", "click"}:
            return False

        signature = self._normalized_action_signature(action)
        if signature[0] in {"navigate", "search", "type"}:
            seen_count = sum(
                1
                for item in history[-10:]
                if self._normalized_action_signature(item.get("action", {})) == signature
            )
            if seen_count >= 2:
                return True

        url_after = self._normalize_url(str(execution_result.get("url_after") or ""))
        if name in {"navigate", "search"} and url_after:
            previous_urls = [
                self._normalize_url(str(item.get("execution_result", {}).get("url_after") or ""))
                for item in history[-8:]
            ]
            if url_after in previous_urls:
                return True

        if url_after and self._is_url_ping_pong_loop(url_after, history):
            return True

        return False

    def _is_url_ping_pong_loop(self, url_after: str, history: list[dict[str, Any]]) -> bool:
        recent_urls = [
            self._normalize_url(str(item.get("execution_result", {}).get("url_after") or ""))
            for item in history[-5:]
        ]
        recent_urls = [url for url in recent_urls if url]
        sequence = [*recent_urls, url_after]
        if len(sequence) < 4:
            return False

        if sequence[-1] == sequence[-3] and sequence[-2] == sequence[-4] and sequence[-1] != sequence[-2]:
            return True

        unique_recent = list(dict.fromkeys(sequence[-5:]))
        return len(unique_recent) <= 2 and len(sequence[-5:]) >= 5

    def _no_change_count(self, history: list[dict[str, Any]], same_page_after_action: bool) -> int:
        if not same_page_after_action:
            return 0

        count = 1
        for item in reversed(history):
            progress = item.get("progress_check", {})
            signals = progress.get("signals", {})
            if not signals.get("same_page_after_action"):
                break
            count += 1
        return count

    def _action_signature(self, action: dict[str, Any]) -> tuple[Any, Any, Any]:
        return (
            action.get("action"),
            action.get("target_id"),
            action.get("text") or "",
        )

    def _normalized_action_signature(self, action: dict[str, Any]) -> tuple[str, Any, str]:
        name = str(action.get("action") or "").lower()
        target_id = action.get("target_id")
        text = str(action.get("text") or "").strip()
        if name == "navigate":
            text = self._normalize_url(text)
            target_id = None
        elif name == "search":
            text = " ".join(text.lower().split())
            target_id = None
        elif name == "type":
            text = " ".join(text.lower().split())
        return (name, target_id, text)

    def _navigation_landed_elsewhere(self, action: dict[str, Any], execution_result: dict[str, Any]) -> bool:
        if str(action.get("action") or "").lower() != "navigate":
            return False
        requested = str(action.get("text") or "").strip()
        if not requested:
            return False
        requested_parsed = urlparse(requested)
        if not requested_parsed.query and not requested_parsed.fragment:
            return False

        url_after = str(execution_result.get("url_after") or "")
        if not url_after:
            return False
        after_parsed = urlparse(url_after.strip())
        if requested_parsed.netloc.lower() != after_parsed.netloc.lower():
            return True
        requested_path = requested_parsed.path.rstrip("/") or "/"
        after_path = after_parsed.path.rstrip("/") or "/"
        if requested_path != after_path:
            return True
        if requested_parsed.query and not after_parsed.query:
            return True
        if requested_parsed.fragment and not after_parsed.fragment:
            return True
        return False

    def _normalize_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url.strip())
        scheme = parsed.scheme.lower()
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/") or "/"
        query = parsed.query.rstrip("&")
        fragment = parsed.fragment.rstrip("&")
        return f"{scheme}://{host}{path}?{query}#{fragment}"

    def _submit_without_navigation(self, action: dict[str, Any], execution_result: dict[str, Any]) -> bool:
        if not (execution_result.get("ok") or execution_result.get("success")):
            return False

        url_before = str(execution_result.get("url_before") or "")
        url_after = str(execution_result.get("url_after") or "")
        if not url_before or url_before != url_after:
            return False

        if not self._looks_like_submit_action(action):
            return False

        return self._looks_like_search_landing_page(url_after)

    def _looks_like_submit_action(self, action: dict[str, Any]) -> bool:
        name = str(action.get("action") or "").lower()
        key = str(action.get("key") or "").lower()
        text = str(action.get("text") or "").lower()

        if name == "press" and key == "enter":
            return True
        if name == "type" and key == "enter":
            return True
        if name == "type" and any(token in text for token in ("google scholar", "search", "site:")):
            return True
        if name == "click":
            return True
        return False

    def _looks_like_search_landing_page(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        if host.endswith("google.com"):
            return path in {"", "/"} and "q" not in query
        if host.endswith("bing.com"):
            return path in {"", "/"} and "q" not in query
        if host.endswith("baidu.com"):
            return path in {"", "/"} and "wd" not in query and "word" not in query
        return False

    def _reason(
        self,
        action_executed: bool,
        made_progress: bool,
        repeated_action: bool,
        action_loop: bool,
        same_page_after_action: bool,
        no_change_count: int,
        submit_without_navigation: bool,
        navigation_landed_elsewhere: bool,
    ) -> str:
        if not action_executed:
            return "action execution failed or was rejected"
        if submit_without_navigation:
            return "search or submit action executed, but the page stayed on the same search landing URL"
        if navigation_landed_elsewhere:
            return "navigation did not land on the requested filtered URL; the site likely normalized or rejected the guessed URL parameters"
        if repeated_action:
            return "same action was repeated too many times"
        if action_loop:
            return "navigation/action loop detected; recent actions revisited the same state without useful task progress"
        if no_change_count >= self.stuck_threshold:
            return "page state did not change for multiple consecutive steps"
        if made_progress:
            return "page state changed after action"
        if same_page_after_action:
            return "action executed but URL, screenshot hash, and candidate count did not change"
        return "progress is uncertain"
