"""Use asyncio for MCP protocol tests on Windows and Linux."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
