from __future__ import annotations

from models.base_client import env_or_default, require_env
from models.openai_compatible_client import OpenAICompatibleClient, OpenAICompatibleConfig


class DeepSeekClient(OpenAICompatibleClient):
    @classmethod
    def from_env(cls) -> "DeepSeekClient":
        return cls(
            OpenAICompatibleConfig(
                api_key=require_env("DEEPSEEK_API_KEY", "deepseek"),
                base_url=env_or_default("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                model=env_or_default("DEEPSEEK_MODEL", "deepseek-chat"),
            )
        )
