"""Explicit .env loading without environment mutation or exposed API keys."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

if TYPE_CHECKING:
    from sim2clip.agent.planner import ModelPlanner


class ConfigurationError(ValueError):
    pass


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    api_key: SecretStr = Field(exclude=True, repr=False)
    base_url: str
    model_name: str

    @field_validator("api_key")
    @classmethod
    def nonempty_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("API key cannot be empty")
        return value

    @field_validator("model_name")
    @classmethod
    def nonempty_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Model name cannot be empty")
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def valid_endpoint(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Base URL must be an HTTP(S) API endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Base URL cannot contain credentials, query or fragment")
        if parsed.port == 0:
            raise ValueError("Base URL port must be positive")
        return value

    @classmethod
    def load(
        cls,
        env_file: Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> "ModelSettings":
        explicit = env_file is not None
        path = env_file if explicit else Path.cwd() / ".env"
        assert path is not None
        if explicit and not path.is_file():
            raise ConfigurationError("Specified .env file does not exist")
        values: dict[str, str | None] = {}
        if path.is_file():
            values.update(dotenv_values(path, encoding="utf-8", interpolate=False))
        values.update(os.environ if environ is None else environ)
        names = {
            "api_key": "MODEL_API_KEY",
            "base_url": "MODEL_BASE_URL",
            "model_name": "MODEL_NAME",
        }
        missing = [name for name in names.values() if not values.get(name, "")]
        if missing:
            raise ConfigurationError("Missing model settings: " + ", ".join(missing))
        try:
            return cls.model_validate({field: values[name] for field, name in names.items()})
        except ValidationError as exc:
            fields = ", ".join(names[str(issue["loc"][0])] for issue in exc.errors())
            raise ConfigurationError(f"Invalid model settings: {fields}") from None


def create_model_planner(settings: ModelSettings, *, timeout: float = 60.0) -> "ModelPlanner":
    from langchain_openai import ChatOpenAI

    from sim2clip.agent.planner import ModelPlanner

    try:
        model = ChatOpenAI(
            model=settings.model_name,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=timeout,
            max_retries=0,
            use_responses_api=False,
            stream_usage=False,
        )
    except Exception:
        raise ConfigurationError("Unable to initialize model; check model configuration") from None
    return ModelPlanner(model, timeout=timeout)
