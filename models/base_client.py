from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TypedDict


class ChatMessage(TypedDict):
    role: str
    content: Any


class ModelClientError(RuntimeError):
    """Raised when the model client cannot complete a request."""


class MissingAPIKeyError(ModelClientError):
    """Raised when the selected provider has no API key configured."""


class BaseChatClient(ABC):
    @abstractmethod
    def complete(self, messages: list[ChatMessage]) -> str:
        raise NotImplementedError


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def require_env(name: str, provider: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingAPIKeyError(
            f"{name} is not set for provider '{provider}'. "
            "Copy .env.example to .env and fill in the required API key."
        )
    return value


def env_or_default(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def compact_json_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return str(value)
