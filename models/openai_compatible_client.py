from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from models.base_client import BaseChatClient, ChatMessage, ModelClientError, env_or_default, require_env


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: int = 60
    temperature: float = 0.0
    max_retries: int = 4


class OpenAICompatibleClient(BaseChatClient):
    def __init__(self, config: OpenAICompatibleConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls) -> "OpenAICompatibleClient":
        return cls(
            OpenAICompatibleConfig(
                api_key=require_env("OPENAI_API_KEY", "openai"),
                base_url=env_or_default("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model=env_or_default("OPENAI_MODEL", "gpt-4o-mini"),
                timeout_seconds=int(env_or_default("OPENAI_TIMEOUT_SECONDS", "120")),
            )
        )

    def complete(self, messages: list[ChatMessage]) -> str:
        url = self._chat_completions_url()
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        last_error: Exception | None = None

        max_retries = max(1, int(self.config.max_retries))
        for attempt in range(max_retries):
            request = urllib.request.Request(
                url=url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                    "Accept-Charset": "utf-8",
                },
                method="POST",
            )

            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    response_data = json.loads(response.read().decode("utf-8"))
                    return self._extract_content(response_data)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if 500 <= exc.code < 600 and attempt < max_retries - 1:
                    last_error = exc
                    time.sleep(1.0 + attempt)
                    continue
                raise ModelClientError(f"Chat completion request failed with HTTP {exc.code}: {body[:1000]}") from exc
            except (urllib.error.URLError, http.client.RemoteDisconnected, ConnectionError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    time.sleep(1.0 + attempt)
                    continue
                reason = getattr(exc, "reason", str(exc))
                raise ModelClientError(f"Chat completion request failed: {reason}") from exc
            except json.JSONDecodeError as exc:
                raise ModelClientError("Model provider returned invalid JSON response") from exc
            except ModelClientError as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    time.sleep(1.0 + attempt)
                    continue
                raise

        raise ModelClientError(f"Chat completion request failed: {last_error}")

    def _chat_completions_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/chat/completions"

    def _extract_content(self, response_data: dict[str, Any]) -> str:
        try:
            content = response_data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelClientError(f"Unexpected chat completion response shape: {response_data}") from exc

        text = self._content_to_text(content)
        if not text:
            raise ModelClientError("Chat completion content is empty or unsupported")
        return text.strip()

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(part for part in parts if part.strip())
        if isinstance(content, dict):
            text = content.get("text") or content.get("content")
            if isinstance(text, str):
                return text
        raise ModelClientError(f"Chat completion content is not text: {type(content).__name__}")
