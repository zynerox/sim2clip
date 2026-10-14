import os
from pathlib import Path

import pytest

from sim2clip.config import ConfigurationError, ModelSettings


def test_dotenv_precedence_secret_redaction_and_no_environment_mutation(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "MODEL_API_KEY=file-key\nMODEL_BASE_URL=http://localhost:1234/v1\nMODEL_NAME=file-model\n",
        encoding="utf-8",
    )
    before = dict(os.environ)
    settings = ModelSettings.load(
        path, environ={"MODEL_API_KEY": "env-key", "MODEL_NAME": "env-model"}
    )
    assert settings.api_key.get_secret_value() == "env-key"
    assert settings.model_name == "env-model"
    assert settings.base_url == "http://localhost:1234/v1"
    assert "env-key" not in repr(settings)
    assert "api_key" not in settings.model_dump()
    assert dict(os.environ) == before


@pytest.mark.parametrize(
    "base",
    [
        "ftp://localhost",
        "not-a-url",
        "https://user:secret@example.com/v1",
        "https://example.com/v1?key=secret",
        "http://localhost:999999",
        "http://localhost:0",
    ],
)
def test_invalid_config_never_exposes_secret(base: str) -> None:
    with pytest.raises(ConfigurationError) as error:
        ModelSettings.load(
            environ={
                "MODEL_API_KEY": "sensitive-key",
                "MODEL_BASE_URL": base,
                "MODEL_NAME": "test-model",
            }
        )
    assert "secret" not in str(error.value) and "sensitive-key" not in str(error.value)
    assert "MODEL_BASE_URL" in str(error.value)


def test_missing_explicit_env_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="does not exist"):
        ModelSettings.load(tmp_path / "missing.env", environ={})
