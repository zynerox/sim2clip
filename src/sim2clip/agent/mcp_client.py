"""MCP boundary. Never import or invoke simulation engine functions here."""

import json
import sys
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import Tool
from pydantic import ValidationError

from sim2clip.agent.state import JSONDict
from sim2clip.scenario_registry import TOOLS, SimulationOutput, tool_contract


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def to_dict(self) -> JSONDict:
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


def default_server() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "sim2clip.mcp_server"])


def parse_result(name: str, payload: JSONDict) -> SimulationOutput:
    if name not in TOOLS:
        raise ToolFailure("UNKNOWN_TOOL", f"Unsupported simulation tool: {name}")
    return tool_contract(name).parse_result(payload)


def validate_discovery(tools: list[Tool]) -> None:
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate MCP tool names")
    if set(names) != set(TOOLS):
        raise ValueError(
            f"MCP tools differ from registry: missing={sorted(set(TOOLS) - set(names))}, "
            f"unregistered={sorted(set(names) - set(TOOLS))}"
        )
    for tool in tools:
        contract = tool_contract(tool.name)
        if (
            tool.input_schema != contract.input_model.model_json_schema()
            or tool.output_schema != contract.output_model.model_json_schema(mode="serialization")
        ):
            raise ValueError(f"MCP contract mismatch: {tool.name}")
        if not tool.description:
            raise ValueError(f"MCP tool lacks a description: {tool.name}")


class SimulationMCPClient:
    def __init__(self, server: Any = None, *, timeout: float = 60.0) -> None:
        self.server = server if server is not None else default_server()
        self.timeout = timeout
        self.client: Client | None = None
        self.tools: list[Tool] = []

    async def __aenter__(self) -> "SimulationMCPClient":
        try:
            self.client = await Client(self.server, read_timeout_seconds=self.timeout).__aenter__()
            self.tools = (await self.client.list_tools()).tools
            validate_discovery(self.tools)
            return self
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        if self.client is not None:
            client, self.client = self.client, None
            await client.__aexit__(None, None, None)

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def call(self, name: str, arguments: JSONDict) -> JSONDict:
        if self.client is None:
            raise RuntimeError("MCP client is not open")
        if name not in TOOLS:
            raise ToolFailure("UNKNOWN_TOOL", f"Unsupported simulation tool: {name}")
        try:
            response = await self.client.call_tool(name, arguments)
        except Exception as exc:
            # MCP 2.3 checks the advertised JSON output schema before returning
            # CallToolResult. Its shape failures are RuntimeError, not transport
            # failures; keep their potentially large payload out of public errors.
            if isinstance(exc, RuntimeError) and str(exc).startswith(
                (
                    f"Invalid structured content returned by tool {name}:",
                    f"Tool {name} has an output schema but did not return structured content",
                )
            ):
                raise ToolFailure(
                    "INVALID_RESULT", "MCP output violates its advertised schema"
                ) from exc
            # The tools are deterministic and idempotent. A transport failure can
            # be retried after reconnecting; deterministic engine errors cannot.
            raise ToolFailure("TRANSPORT_ERROR", type(exc).__name__, retryable=True) from exc
        payload = response.structured_content
        if payload is None:
            try:
                text = next(block.text for block in response.content if block.type == "text")
                payload = json.loads(text)
            except (StopIteration, ValueError) as exc:
                if response.is_error:
                    raise ToolFailure("TOOL_ERROR", "MCP tool failed without a JSON error") from exc
                raise ToolFailure("INVALID_RESULT", "MCP response has no JSON object") from exc
        if not isinstance(payload, dict):
            raise ToolFailure("INVALID_RESULT", "MCP response must be an object")
        if response.is_error:
            error = payload.get("error", {})
            if not isinstance(error, dict):
                error = {}
            raise ToolFailure(
                str(error.get("code", "TOOL_ERROR")),
                str(error.get("message", "MCP tool failed")),
                retryable=error.get("retryable") is True,
            )
        try:
            return parse_result(name, payload).model_dump(mode="json")
        except ValidationError as exc:
            raise ToolFailure("INVALID_RESULT", "MCP output violates the shared schema") from exc

    async def reconnect(self) -> None:
        await self.close()
        await self.__aenter__()
