from __future__ import annotations

from models.base_client import env_or_default, require_env
from models.openai_compatible_client import OpenAICompatibleClient, OpenAICompatibleConfig


class GeminiClient(OpenAICompatibleClient):
    """Gemini 视觉模型客户端，通过 OpenAI 兼容 API 调用。

    使用 OPENAI_API_KEY / OPENAI_BASE_URL 环境变量，
    模型名从 GEMINI_MODEL 环境变量读取，默认为 gemini-3.5-flash。
    """

    @classmethod
    def from_env(cls) -> "GeminiClient":
        return cls(
            OpenAICompatibleConfig(
                api_key=require_env("OPENAI_API_KEY", "gemini"),
                base_url=env_or_default("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model=env_or_default("GEMINI_MODEL", "gemini-3.5-flash"),
                timeout_seconds=int(env_or_default("GEMINI_TIMEOUT_SECONDS", env_or_default("OPENAI_TIMEOUT_SECONDS", "120"))),
            )
        )
