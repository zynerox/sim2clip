"""Isolate SDK cancellation in a killable process; print resource diagnostics."""

import argparse
import asyncio
import ctypes
import faulthandler
import json
import os
import sys
import threading
from pathlib import Path

import anyio
from mcp.client.stdio import StdioServerParameters

from sim2clip.agent.mcp_client import SimulationMCPClient, ToolFailure


def alive(pid):
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def probe(case, log):
    baseline = set(asyncio.all_tasks())
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("cancellation_server.py"))],
        env={"M5_LOG": str(log), "M5_MODE": case},
    )
    case = case.removeprefix("worker_")
    if case == "discovery":
        try:
            async with SimulationMCPClient(parameters):
                raise AssertionError("Discovery should fail")
        except ValueError as exc:
            assert "missing=" in str(exc)
    elif case in {"outer_anyio", "outer_asyncio"}:
        if case == "outer_anyio":
            with anyio.move_on_after(None) as scope:
                async with SimulationMCPClient(parameters) as client:
                    scope.deadline = anyio.current_time() + 0.1
                    await client.call("run_server_simulation", {"requests": 3})
            assert scope.cancelled_caught
        else:
            try:
                async with asyncio.timeout(None) as timeout:
                    async with SimulationMCPClient(parameters) as client:
                        timeout.reschedule(asyncio.get_running_loop().time() + 0.1)
                        await client.call("run_server_simulation", {"requests": 3})
            except TimeoutError:
                pass
            else:
                raise AssertionError("Native caller should time out")
    else:
        async with SimulationMCPClient(parameters) as client:
            if case == "normal":
                await client.call("run_server_simulation", {"requests": 10})
            elif case == "native_task":
                task = asyncio.create_task(client.call("run_server_simulation", {"requests": 3}))
                await anyio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await client.call("run_server_simulation", {"requests": 2})
            elif case == "inner_anyio":
                with anyio.move_on_after(0.1) as scope:
                    await client.call("run_server_simulation", {"requests": 3})
                assert scope.cancelled_caught
                await client.call("run_server_simulation", {"requests": 2})
            else:
                # Change only the SDK request deadline after discovery has succeeded.
                if case == "timeout":
                    client.client._session._session_read_timeout_seconds = 0.1
                try:
                    await client.call("run_server_simulation", {"requests": 3})
                except ToolFailure as exc:
                    assert exc.code == "TRANSPORT_ERROR" and exc.retryable
                else:
                    raise AssertionError("Expected transport failure")
                await client.reconnect()
                await client.call("run_server_simulation", {"requests": 2})
    await anyio.sleep(0.05)
    remaining = [t for t in asyncio.all_tasks() - baseline if not t.done()]
    assert not remaining, [(t.get_name(), str(t.get_coro())) for t in remaining]
    pids = [int(line[4:]) for line in log.read_text().splitlines() if line.startswith("pid:")]
    assert pids and not any(alive(pid) for pid in pids), pids
    return {"case": case, "server_pids": pids, "live_child_processes": 0, "pending_async_tasks": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("case")
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    faulthandler.dump_traceback_later(12, repeat=True)
    before = set(threading.enumerate())
    result = anyio.run(probe, args.case, args.log)
    threads = [t.name for t in threading.enumerate() if t not in before and t.is_alive()]
    assert not threads, threads
    result["remaining_threads"] = threads
    faulthandler.cancel_dump_traceback_later()
    print(json.dumps(result))
