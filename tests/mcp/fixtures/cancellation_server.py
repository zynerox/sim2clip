"""Real stdio fixture: delayed handler, abrupt exit and failed discovery."""

import os
import time
from pathlib import Path

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import ListToolsResult

from sim2clip.mcp_server import simulation_server as production


def record(value):
    with Path(os.environ["M5_LOG"]).open("a", encoding="utf-8") as stream:
        stream.write(value + "\n")


async def list_tools(ctx, params):
    tools = production._tool_definitions()
    return ListToolsResult(tools=tools[:-1] if os.environ["M5_MODE"] == "discovery" else tools)


async def call(ctx, params):
    record("call:" + params.name)
    if params.arguments.get("requests") == 3:
        if os.environ["M5_MODE"] == "exit":
            os._exit(7)
        try:
            if os.environ["M5_MODE"].startswith("worker_"):
                record("worker_started")
                await anyio.to_thread.run_sync(lambda: time.sleep(10))
            else:
                await anyio.sleep(2)
        except BaseException:
            record("handler_cancelled")
            raise
    return await production._call_tool(ctx, params)


async def main():
    record("pid:" + str(os.getpid()))
    server = Server("m5-cancellation", on_list_tools=list_tools, on_call_tool=call)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
