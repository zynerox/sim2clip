"""Local acceptance driver. No paid model calls; each subprocess has a watchdog.

Run with the project venv; --python can target an isolated wheel installation.
Use a NEW --output directory per run. Large comparison probes have an RSS cap.
"""

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def memory(pid):
    """Windows process RSS and OS peak working set, not total machine memory."""
    if os.name != "nt":
        return None

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        return None
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    okay = ctypes.WinDLL("psapi").GetProcessMemoryInfo(
        ctypes.c_void_p(handle), ctypes.byref(counters), counters.cb
    )
    kernel.CloseHandle(ctypes.c_void_p(handle))
    return (counters.WorkingSetSize, counters.PeakWorkingSetSize) if okay else None


def command(args, directory, label, *, timeout=180, rss_limit=None, env=None):
    start = time.perf_counter()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MODEL_") and key != "PYTHONPATH"
    }
    environment.update(
        PYTHONIOENCODING="utf-8",
        NO_PROXY="127.0.0.1,localhost",
        LANGCHAIN_TRACING_V2="false",
        LANGSMITH_TRACING="false",
    )
    environment.pop("LANGSMITH_API_KEY", None)
    environment.update(env or {})
    with (
        (directory / f"{label}.stdout.log").open("wb") as out,
        (directory / f"{label}.stderr.log").open("wb") as err,
    ):
        child = subprocess.Popen(args, cwd=directory, env=environment, stdout=out, stderr=err)
        peak = 0
        aborted = None
        while child.poll() is None:
            pidfile = directory / f"{label}.pid"
            worker_pid = int(pidfile.read_text()) if pidfile.exists() else child.pid
            measured = memory(worker_pid)
            if measured:
                peak = max(peak, measured[1])
            if time.perf_counter() - start > timeout:
                aborted = "timeout"
            if rss_limit and measured and measured[0] > rss_limit:
                aborted = "rss_limit"
            if aborted:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                    )
                else:
                    child.kill()
                child.wait(timeout=10)
                break
            time.sleep(0.05)
    row = {
        "command": args,
        "cwd": str(directory),
        "exit_code": child.returncode,
        "seconds": time.perf_counter() - start,
        "peak_working_set_bytes": peak or None,
        "rss_limit_bytes": rss_limit,
        "aborted": aborted,
    }
    write(directory / f"{label}.command.json", row)
    if aborted or child.returncode:
        raise RuntimeError(f"{label} failed; inspect saved logs: {row}")
    return row


def e2e(args):
    from sim2clip.scenario_registry import validate_tool_response
    from sim2clip.visualization.data import load_result

    spec = importlib.util.spec_from_file_location("m5_endpoint", ROOT / "tests/cli/conftest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = []
    cases = [
        ("queue", "100个顾客，比较1个收银台和3个收银台", None),
        ("capacity", "1000个请求，比较1台和3台服务器的平均响应时间", "server_capacity"),
        (
            "strategy",
            "1000个请求，比较 Round Robin 和 Least Outstanding 的平均响应时间",
            "server_strategy",
        ),
        (
            "heterogeneous",
            "1000个请求，比较异构服务器池上的 Round Robin 和 Least Outstanding",
            "server_heterogeneous",
        ),
    ]
    for label, query, mode in cases:
        directory = args.output / label
        directory.mkdir()
        endpoint = module.ModelEndpoint([mode]) if mode else None
        try:
            credentials = (
                {}
                if endpoint is None
                else {
                    "MODEL_API_KEY": "m5-fixture-key-not-real",
                    "MODEL_BASE_URL": endpoint.base_url,
                    "MODEL_NAME": "m5-protocol-fixture",
                }
            )
            cmd = [
                str(args.python),
                "-m",
                "sim2clip",
                "run",
                query,
                "--planner",
                "demo" if endpoint is None else "model",
                "--thread-id",
                label,
                "--output",
                str(directory / "output"),
                "--json",
            ]
            row = command(cmd, directory, "run", env=credentials)
            summary = json.loads((directory / "run.stdout.log").read_text(encoding="utf-8"))
            assert summary["status"] == "completed", summary
            result = load_result(Path(summary["artifacts"]["result"]))
            private = json.loads(
                (directory / "output/runs" / label / "state.json").read_text(encoding="utf-8")
            )
            last = private["tool_calls"][-1]
            validate_tool_response(last["name"], last["arguments"], result)
            for call in private["tool_calls"]:
                reference = call["response"]
                assert (
                    hashlib.sha256(Path(reference["path"]).read_bytes()).hexdigest()
                    == reference["sha256"]
                )
            if endpoint:
                assert len(endpoint.requests) == 1
                assert len(endpoint.requests[0]["body"]["tools"]) == 4
            artifacts = {
                key: {
                    "path": path,
                    "bytes": Path(path).stat().st_size,
                    "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                }
                for key, path in summary["artifacts"].items()
                if isinstance(path, str) and Path(path).is_file()
            }
            row.update(
                status=summary["status"],
                scenario_id=summary["scenario_id"],
                planning_rounds=summary["planning_rounds"],
                tool_attempts=summary["tool_attempts"],
                seeds=list(result.seeds),
                result_id=result.comparison_id,
                summaries=[s.model_dump(mode="json") for s in result.summaries],
                artifacts=artifacts,
                native_tool_calling="Mock HTTP protocol + real SDK"
                if endpoint
                else "explicit fixed DemoPlanner",
                mcp="real stdio child with independently verified engine results",
                gif=summary["visualization"]["gif"],
            )
            resume = command(
                [
                    str(args.python),
                    "-m",
                    "sim2clip",
                    "resume",
                    label,
                    "--output",
                    str(directory / "output"),
                    "--json",
                ],
                directory,
                "resume",
                timeout=120,
            )
            resumed = json.loads((directory / "resume.stdout.log").read_text(encoding="utf-8"))
            assert (
                resumed["status"] == "completed"
                and resumed["tool_attempts"] == summary["tool_attempts"]
            )
            assert not endpoint or len(endpoint.requests) == 1
            row["resume"] = resume
            rows.append(row)
            write(args.output / "e2e.json", rows)
            print(label, row["seconds"], "verified", flush=True)
        finally:
            if endpoint:
                endpoint.close()


def probe(args):
    import faulthandler

    from sim2clip.agent.server_validation import (
        validate_server_comparison,
        validate_server_simulation,
    )
    from sim2clip.scenarios.server_contracts import ServerComparisonRequest, ServerSimulationConfig
    from sim2clip.simulation.server_comparison import run_server_comparison
    from sim2clip.simulation.server_model import run_server_simulation
    from sim2clip.visualization.html import render_html

    faulthandler.dump_traceback_later(40, repeat=True)
    start = time.perf_counter()
    config = ServerSimulationConfig(requests=args.count)
    if args.queue:
        from sim2clip.agent.validation import validate_simulation
        from sim2clip.simulation import SimulationConfig, run_simulation

        result = run_simulation(SimulationConfig(cashiers=3, customers=args.count))
    elif args.budget:
        request = ServerComparisonRequest.model_validate(
            {
                "comparison_axis": "server_pool",
                "seeds": list(range(42, 47)),
                "experiments": [
                    {"group_id": "one", "config": config.model_dump(mode="json")},
                    {
                        "group_id": "three",
                        "config": {
                            **config.model_dump(mode="json"),
                            "servers": [{"server_id": i} for i in (1, 2, 3)],
                        },
                    },
                ],
            }
        )
        result = run_server_comparison(request)
    else:
        result = run_server_simulation(config)
    engine = time.perf_counter() - start
    start = time.perf_counter()
    if args.queue:
        validate_simulation(result)
    elif args.budget:
        validate_server_comparison(result)
    else:
        validate_server_simulation(result)
    validation = time.perf_counter() - start
    start = time.perf_counter()
    payload = result.model_dump_json().encode()
    row = {
        "requests_per_run": args.count,
        "scenario_id": "queue" if args.queue else "server_load_balancing",
        "total_request_runs": args.count * (10 if args.budget else 1),
        "events": args.count * (3 if args.queue else 40 if args.budget else 4),
        "engine_seconds": engine,
        "validation_seconds": validation,
        "serialization_seconds": time.perf_counter() - start,
        "compact_json_bytes": len(payload),
        "peak_working_set_bytes": memory(os.getpid())[1],
        "schema_version": "1",
    }
    if not args.budget:
        (args.output / "result.json").write_bytes(payload)
        start = time.perf_counter()
        html = render_html(result).encode()
        (args.output / "visualization.html").write_bytes(html)
        row.update(
            html_bytes=len(html),
            html_seconds=time.perf_counter() - start,
            peak_working_set_bytes=memory(os.getpid())[1],
        )
    else:
        row["html_status"] = (
            "not rendered at full limit; see measured smaller bundles and resource estimate"
        )
    write(args.output / "metrics.json", row)
    faulthandler.cancel_dump_traceback_later()
    print(json.dumps(row), flush=True)


def performance(args):
    rows = []
    cases = ((3, False), (1000, False), (10000, False))
    if not args.queue:
        cases += ((10000, True),)
    for count, budget in cases:
        directory = args.output / ("budget-100000" if budget else f"single-{count}")
        directory.mkdir()
        cmd = (
            [
                str(args.python),
                str(Path(__file__).resolve()),
                "probe",
                "--output",
                str(directory),
                "--count",
                str(count),
            ]
            + (["--budget"] if budget else [])
            + (["--queue"] if args.queue else [])
        )
        execution = command(cmd, directory, "probe", timeout=180, rss_limit=1536 * 1024**2)
        row = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        row["controlled_process"] = execution
        rows.append(row)
        write(args.output / "performance.json", rows)
        print(count, budget, row, flush=True)


def browser(args):
    from playwright.sync_api import sync_playwright

    paths = [p for p in args.source.rglob("visualization.html") if "installed-env" not in p.parts]
    rows = []
    with sync_playwright() as p:
        instance = p.chromium.launch(
            headless=True, executable_path="C:/Program Files/Google/Chrome/Application/chrome.exe"
        )
        for index, path in enumerate(paths):
            page = instance.new_page(viewport={"width": 1440, "height": 1120})
            errors, network = [], []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            page.on(
                "request",
                lambda r, network=network: (
                    network.append(r.url) if not r.url.startswith("file:") else None
                ),
            )
            start = time.perf_counter()
            page.goto(path.as_uri(), timeout=60000)
            page.evaluate("window.Sim2Clip.ready || Promise.resolve()")
            initialization = time.perf_counter() - start
            assert not page.locator("#error").is_visible(), page.locator("#error").inner_text()
            assert not errors and not network, (errors, network)
            times = page.evaluate(
                "(()=>{const h=Sim2Clip.getState().endTime; return [0,h*.4,h*.7,0,h];})()"
            )
            vectors = []
            timings = []
            for t in times * 6:
                milliseconds = page.evaluate(
                    "t=>{const s=performance.now();Sim2Clip.pause();Sim2Clip.seek(t);"
                    "return performance.now()-s;}",
                    t,
                )
                timings.append(milliseconds)
            for t in times:
                page.evaluate("t=>Sim2Clip.seek(t)", t)
                a = page.evaluate("Sim2Clip.getScenePoses()")
                page.evaluate("Sim2Clip.seek(0)")
                page.evaluate("t=>Sim2Clip.seek(t/2)", t)
                page.evaluate("t=>Sim2Clip.seek(t)", t)
                assert a == page.evaluate("Sim2Clip.getScenePoses()")
                state = page.evaluate("Sim2Clip.getState()")
                assert all(s["timestamp"] == state["clock"] for s in state["snapshots"])
                vectors.append({"clock": t, "snapshots": state["snapshots"]})
            page.screenshot(path=str(args.output / f"{index}-complete.png"), full_page=True)
            session = page.context.new_cdp_session(page)
            session.send("Performance.enable")
            metrics = {
                v["name"]: v["value"] for v in session.send("Performance.getMetrics")["metrics"]
            }
            row = {
                "html": str(path),
                "chromium": instance.version,
                "initialization_seconds": initialization,
                "seek_median_ms": median(timings),
                "seek_max_ms": max(timings),
                "js_heap_used_bytes": metrics.get("JSHeapUsedSize"),
                "external_requests": network,
                "page_errors": errors,
                "deterministic_seek": True,
            }
            rows.append(row)
            write(args.output / f"{index}-vectors.json", vectors)
            write(args.output / "browser.json", rows)
            print(path.parent.name, row, flush=True)
            page.close()
        instance.close()


def installed(args):
    script = """
import asyncio, hashlib, json
from pathlib import Path
import sim2clip
from sim2clip.agent.mcp_client import SimulationMCPClient
from sim2clip.scenario_registry import TOOLS, validate_tool_response
from sim2clip.visualization.pipeline import export_demo
async def main():
    root=Path(sim2clip.__file__).parent
    assert "site-packages" in str(root) and "src" not in root.parts
    definitions=[]
    requests={"run_simulation":{"customers":3,"cashiers":1},"compare_simulations":{"experiments":[{"customers":3,"cashiers":1},{"customers":3,"cashiers":3}],"seeds":[42]},"run_server_simulation":{"requests":3},"compare_server_simulations":{"comparison_axis":"routing_strategy","seeds":[42],"experiments":[{"group_id":"rr","config":{"requests":3}},{"group_id":"lo","config":{"requests":3,"routing_strategy":"least_outstanding"}}]}}
    async with SimulationMCPClient() as client:
        assert set(t.name for t in client.tools)==set(TOOLS)
        for t in client.tools:
            definitions.append(t.model_dump(mode="json",by_alias=True))
        for name,arguments in requests.items():
            raw=await client.call(name,arguments)
            result=TOOLS[name].parse_result(raw)
            validate_tool_response(name,arguments,result)
            export_demo(result,Path(name),frames=3)
    assets={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root/"visualization/assets").iterdir() if p.is_file()}
    print(json.dumps({"package":str(root),"assets":assets,"tools":definitions,"real_stdio_calls":list(requests)}))
asyncio.run(main())
"""
    path = args.output / "installed_probe.py"
    path.write_text(script, encoding="utf-8")
    command([str(args.python), str(path)], args.output, "installed", timeout=90)
    command(
        [str(args.python.with_name("sim2clip.exe")), "--version"],
        args.output,
        "entrypoint",
        timeout=20,
    )
    write(
        args.output / "installed.json",
        json.loads((args.output / "installed.stdout.log").read_text(encoding="utf-8")),
    )


def legacy(args):
    import sqlite3

    from sim2clip.agent import DemoPlanner, open_workflow
    from sim2clip.visualization.data import load_result
    from sim2clip.visualization.pipeline import export_demo

    async def recover():
        rows = []
        cases = [
            (
                "step5",
                ROOT / "outputs/cli-demo/checkpoints.sqlite",
                ROOT / "outputs/cli-demo/runs/step5-demo/state.json",
                ROOT / "outputs/cli-demo/runs",
            ),
            (
                "m3-queue",
                ROOT / "outputs/multi-scenario-m3/final-proof/checkpoints.sqlite",
                ROOT / "outputs/multi-scenario-m3/final-proof/queue-state.json",
                ROOT / "outputs/multi-scenario-m3/final-proof/runs",
            ),
            (
                "m3-server",
                ROOT / "outputs/multi-scenario-m3/final-proof/checkpoints.sqlite",
                ROOT / "outputs/multi-scenario-m3/final-proof/server_load_balancing-state.json",
                ROOT / "outputs/multi-scenario-m3/final-proof/runs",
            ),
        ]
        for label, original, saved, artifacts in cases:
            directory = args.output / label
            directory.mkdir()
            state = json.loads(saved.read_text(encoding="utf-8"))
            checksum = hashlib.sha256(original.read_bytes()).hexdigest()
            checkpoint = directory / "copy.sqlite"
            with (
                sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as source,
                sqlite3.connect(checkpoint) as destination,
            ):
                source.backup(destination)
            with sqlite3.connect(checkpoint) as connection:
                before = connection.execute("SELECT * FROM checkpoints").fetchall()
            async with open_workflow(
                DemoPlanner(), checkpoint_path=checkpoint, artifact_root=artifacts
            ) as workflow:
                after = await workflow.resume(thread_id=state["run_id"])
            assert after["status"] == "completed"
            assert after["tool_calls"] == state["tool_calls"]
            assert after["state_version"] == "2" and after["schema_version"] == "1"
            with sqlite3.connect(checkpoint) as connection:
                assert before == connection.execute("SELECT * FROM checkpoints").fetchall()
            assert hashlib.sha256(original.read_bytes()).hexdigest() == checksum
            rows.append(
                {
                    "case": label,
                    "original_checkpoint": str(original),
                    "original_sha256": checksum,
                    "saved_state_sha256": hashlib.sha256(saved.read_bytes()).hexdigest(),
                    "had_scenario_id": "scenario_id" in state,
                    "status": after["status"],
                    "scenario_id": after["scenario_id"],
                    "checkpoint_rows_preserved": True,
                    "tool_history_unchanged": True,
                    "tool_attempts": len(after["tool_calls"]),
                }
            )
        path = ROOT / "docs/assets/demo-result.json"
        result = load_result(path)
        exported = export_demo(result, args.output / "step6-json", frames=21)
        rows.append(
            {
                "case": "actual-step6-json",
                "original_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "historical_commit": "d38e2ea",
                "same_as_saved_lunch_demo": path.read_bytes()
                == (ROOT / "outputs/lunch-demo-step6/result.json").read_bytes(),
                "artifacts": exported,
            }
        )
        write(args.output / "legacy.json", rows)
        print(json.dumps(rows, ensure_ascii=False))

    import asyncio

    asyncio.run(recover())


def suite(args):
    import shutil

    # Test helpers use repo-relative paths; log files live in a fresh QA directory.
    try:
        result = command(
            [
                str(args.python),
                "-m",
                "pytest",
                "-q",
                "-o",
                "faulthandler_timeout=60",
                "-o",
                "junit_family=legacy",
                f"--junitxml={args.output / 'pytest.xml'}",
            ],
            ROOT,
            "m5-suite",
            timeout=960,
        )
    finally:
        for suffix in ("stdout.log", "stderr.log", "command.json"):
            source = ROOT / f"m5-suite.{suffix}"
            if source.exists():
                shutil.move(str(source), args.output / f"pytest.{suffix}")
    write(args.output / "suite.json", result)
    print((args.output / "pytest.stdout.log").read_text(encoding="utf-8")[-1200:])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("e2e", "performance", "probe", "browser", "installed", "legacy", "suite")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--budget", action="store_true")
    parser.add_argument("--queue", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.python = args.python.resolve()
    if args.source is not None:
        args.source = args.source.resolve()
    if args.mode != "probe":
        args.output.mkdir(parents=True, exist_ok=False)
    else:
        (args.output / "probe.pid").write_text(str(os.getpid()))
    globals()[args.mode](args)
