"""R1 preparation by default. Remote stages require explicit operator flags.

Do not run smoke/full/reverify until the human has authorized that particular stage.
Each case is a separate process, with a hard watchdog and durable request count.
"""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from langsmith import tracing_context

from sim2clip.agent.artifacts import json_bytes
from sim2clip.config import ModelSettings
from sim2clip.r1 import (
    CASES,
    REVERIFY_CASES,
    SMOKE_IDS,
    Audit,
    Limits,
    configuration_review,
    cost_cny,
    run_case,
)


def stop_child(child: subprocess.Popen[bytes]) -> None:
    """Only this case's newly spawned process tree; never a global process match."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    else:
        child.kill()
    child.wait(timeout=10)


def stage(args: argparse.Namespace) -> dict[str, Any]:
    if not args.authorize_remote:
        raise ValueError("Remote calls require explicit authorization for this stage")
    full = args.command == "full"
    reverify = args.command == "reverify"
    if full:
        if args.smoke_proof is None:
            raise ValueError("Full R1 requires a passed real smoke proof and renewed authorization")
        proof = json.loads(args.smoke_proof.read_text(encoding="utf-8"))
        if (
            proof.get("stage") != "smoke"
            or proof.get("evidence_type") != "real_remote"
            or not proof.get("passed")
        ):
            raise ValueError("Smoke proof must describe passed real remote A/B/E")
        if {c.get("case_id") for c in proof.get("cases", [])} != set(SMOKE_IDS) or not all(
            c.get("acceptance_passed") for c in proof["cases"]
        ):
            raise ValueError("Smoke proof does not cover the approved cases")
    if reverify:
        proof = json.loads(args.r1_proof.read_text(encoding="utf-8"))
        expected = {case.id for case in CASES if case.id not in SMOKE_IDS}
        if (
            proof.get("stage") != "full"
            or proof.get("evidence_type") != "real_remote"
            or not proof.get("passed")
            or {case.get("case_id") for case in proof.get("cases", [])} != expected
            or not all(case.get("acceptance_passed") for case in proof.get("cases", []))
        ):
            raise ValueError("Reverification requires completed real R1 round-two evidence")
    review = configuration_review(args.env_file)
    if not (
        review.get("configuration_valid")
        and review.get("model_matches")
        and review.get("endpoint_matches")
    ):
        raise ValueError("Prepare configuration first; no remote request was sent")
    root: Path = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    limits = Limits(total_requests=4 if reverify else 14 if full else 6, case_requests=2)
    audit = Audit(root, limits, remote=True)
    audit.save()
    ids = (
        [case.id for case in REVERIFY_CASES]
        if reverify
        else [c.id for c in CASES if (c.id not in SMOKE_IDS if full else c.id in SMOKE_IDS)]
    )
    rows = []
    for case_id in ids:
        argv = [
            sys.executable,
            str(Path(__file__).resolve()),
            "_worker",
            "--case",
            case_id,
            "--output",
            str(root),
            "--total-requests",
            str(limits.total_requests),
            "--case-requests",
            str(limits.case_requests),
            "--authorize-remote",
        ]
        if args.env_file:
            argv += ["--env-file", str(args.env_file.resolve())]
        if reverify:
            argv += ["--reverify"]
        environment = dict(os.environ)
        environment.update(
            LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false", PYTHONIOENCODING="utf-8"
        )
        environment.pop("LANGSMITH_API_KEY", None)
        start = time.perf_counter()
        # Raw SDK/provider output is intentionally not captured into log files.
        child = subprocess.Popen(
            argv, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            child.wait(timeout=limits.case_seconds)
        except subprocess.TimeoutExpired:
            stop_child(child)
            audit = Audit(root, limits, remote=True)
            audit.stop("CASE_WATCHDOG_TIMEOUT")
        audit = Audit(root, limits, remote=True)
        path = root / case_id / "summary.json"
        row = (
            json.loads(path.read_text(encoding="utf-8"))
            if path.is_file()
            else {
                "case_id": case_id,
                "acceptance_passed": False,
                "status": "failed",
                "error_type": "CASE_WATCHDOG_TIMEOUT"
                if audit.data["stop_reason"] == "CASE_WATCHDOG_TIMEOUT"
                else "WORKER_EXIT_ERROR",
                "seconds": time.perf_counter() - start,
            }
        )
        if not path.is_file():
            audit.store.write(path, json_bytes(row))
        rows.append(row)
        audit.store.write(root / "progress.json", json_bytes({"completed_cases": rows}))
        print(
            json.dumps(
                {
                    "case_id": case_id,
                    "status": row["status"],
                    "acceptance_passed": row["acceptance_passed"],
                    "requests_used": len(audit.data["requests"]),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if (
            audit.data["stop_reason"]
            or child.returncode != 0
            or len(audit.data["requests"]) >= limits.total_requests
        ):
            break
    requests = audit.data["requests"]
    summary = {
        "stage": args.command,
        "evidence_type": "real_remote",
        "model": audit.data["model"],
        "passed": len(rows) == len(ids) and all(r["acceptance_passed"] for r in rows),
        "cases": rows,
        "not_run": [i for i in ids if i not in {r["case_id"] for r in rows}],
        "request_count": len(requests),
        "request_limit": limits.total_requests,
        "gross_cost_cny_known": sum(r["gross_cost_cny"] or 0 for r in requests),
        "cost_complete": all(r["gross_cost_cny"] is not None for r in requests),
        "actual_bill_cny": None,
        "free_quota_remaining": None,
        "stop_reason": audit.data["stop_reason"],
        "next_stage_authorized": False,
    }
    audit.store.write(root / "summary.json", json_bytes(summary))
    return summary


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="R1 本地准备 / 经独立授权的百炼远程验收")
    sub = p.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="只检查配置和计划，不调用远程 API")
    prepare.add_argument("--env-file", type=Path)
    for name in ("smoke", "full", "reverify", "_worker"):
        run = sub.add_parser(name)
        run.add_argument("--authorize-remote", action="store_true")
        run.add_argument("--env-file", type=Path)
        run.add_argument("--output", type=Path, required=True)
        if name == "full":
            run.add_argument("--smoke-proof", type=Path, required=True)
        else:
            run.set_defaults(smoke_proof=None)
        if name == "reverify":
            run.add_argument("--r1-proof", type=Path, required=True)
        if name == "_worker":
            run.add_argument("--case", choices=[c.id for c in CASES], required=True)
            run.add_argument("--total-requests", type=int, required=True)
            run.add_argument("--case-requests", type=int, required=True)
            run.add_argument("--reverify", action="store_true")
    return p


def main() -> int:
    logging.disable(logging.CRITICAL)
    os.environ.update(LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false")
    args = parser().parse_args()
    try:
        if args.command == "prepare":
            result = {
                "stage": "local_preparation",
                "remote_requests": 0,
                "configuration": configuration_review(args.env_file),
                "smoke_cases": list(SMOKE_IDS),
                "request_limit": 6,
                "output_token_parameter": {"max_completion_tokens": 4096},
                "enable_thinking": False,
                "model_timeout_seconds": 60,
                "case_watchdog_seconds": 240,
                "gross_cny_estimate_6_requests_12000_input_4096_output_each": cost_cny(12000, 4096)
                * 6,
                "free_allowance_and_actual_bill": "not_verified_check_bailian_console",
                "r1_1_reverification": {
                    "status": "awaiting_new_authorization",
                    "cases": [case.id for case in REVERIFY_CASES],
                    "request_limit": 4,
                    "case_request_limit": 2,
                    "gross_cny_estimate_4_requests_12000_input_4096_output_each": cost_cny(
                        12000, 4096
                    )
                    * 4,
                },
            }
        elif args.command == "_worker":
            if not args.authorize_remote:
                raise ValueError("Worker is not authorized")
            limits = Limits(total_requests=args.total_requests, case_requests=args.case_requests)
            if args.reverify and (
                args.case not in {case.id for case in REVERIFY_CASES} or limits.total_requests > 4
            ):
                raise ValueError(
                    "Reverification worker permits only G/H with at most four requests"
                )
            audit = Audit(args.output, limits, remote=True)
            case = next(
                c for c in (REVERIFY_CASES if args.reverify else CASES) if c.id == args.case
            )
            with tracing_context(enabled=False):
                result = asyncio.run(run_case(case, ModelSettings.load(args.env_file), audit))
        else:
            result = stage(args)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        if args.command in {"smoke", "full", "reverify"} and not result["passed"]:
            return 1
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "R1_CONFIGURATION_OR_RUNTIME_ERROR",
                    "note": "No raw provider error or credential is printed",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
