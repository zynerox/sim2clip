"""HTTP protocol fixture, not a real language model; MCP and SimPy stay real."""

import json
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest


class ModelEndpoint:
    def __init__(self, responses: list[str], *, timeout_delay: float = 0.5) -> None:
        self.responses = responses
        self.timeout_delay = timeout_delay
        self.requests: list[dict[str, Any]] = []
        self.headers: list[str | None] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append({"path": self.path, "body": body})
                owner.headers.append(self.headers.get("Authorization"))
                index = len(owner.requests) - 1
                mode = owner.responses[min(index, len(owner.responses) - 1)]
                if mode == "timeout":
                    time.sleep(owner.timeout_delay)
                envelope: dict[str, Any] = {
                    "experiment_goal": "比较12位顾客在2与4个收银台下的平均等待时间",
                    "rationale": "同样的人群与配对种子用于比较服务容量",
                    "assumptions": ["指数到达均值10秒，服务均值25秒"],
                    "request": {
                        "experiments": [{"cashiers": c, "customers": 12} for c in (2, 4)],
                        "seeds": [42] if mode == "pilot" else [42, 43, 44, 45, 46],
                    },
                }
                if mode == "extra_results":
                    envelope["metrics"] = {"average_wait_time": 0}
                if mode == "bad_parameters":
                    envelope["request"]["experiments"][0]["customers"] = -12
                if mode in (
                    "server",
                    "server_pilot",
                    "server_capacity",
                    "server_strategy",
                    "server_heterogeneous",
                    "server_sensitive",
                ):
                    seeds = [42] if mode == "server_pilot" else [42, 43, 44, 45, 46]
                    config = {
                        "requests": 30
                        if mode in ("server", "server_pilot", "server_sensitive")
                        else 1000,
                        "seed": seeds[0],
                        "servers": [{"server_id": 1}, {"server_id": 2}],
                    }
                    envelope = {
                        "experiment_goal": "比较分流策略对平均响应时间的影响",
                        "rationale": "固定请求样本上的 RR/LO 配对比较",
                        "assumptions": ["指数到达0.01秒，工作量均值0.025，速率1"],
                        "request": {
                            "comparison_axis": "routing_strategy",
                            "seeds": seeds,
                            "experiments": [
                                {"group_id": "rr", "config": config},
                                {
                                    "group_id": "lo",
                                    "config": {**config, "routing_strategy": "least_outstanding"},
                                },
                            ],
                        },
                    }
                    if mode == "server_capacity":
                        envelope["request"]["comparison_axis"] = "server_pool"
                        first, second = envelope["request"]["experiments"]
                        first["group_id"] = "one"
                        first["config"] = {**config, "servers": [{"server_id": 1}]}
                        second["group_id"] = "three"
                        second["config"] = {
                            **config,
                            "servers": [{"server_id": i} for i in (1, 2, 3)],
                        }
                    if mode == "server_heterogeneous":
                        for experiment in envelope["request"]["experiments"]:
                            experiment["config"]["servers"] = [
                                {"server_id": 1, "processing_rate": 1.0},
                                {"server_id": 2, "processing_rate": 2.0},
                            ]
                if mode == "server_sensitive":
                    for field in ("experiment_goal", "rationale"):
                        envelope[field] = "private-model-canary-123"
                    envelope["assumptions"] = ["private-model-canary-123"]
                if mode in {"queue_single_sensitive", "server_single_sensitive"}:
                    envelope = {
                        "experiment_goal": "private-model-canary-123",
                        "rationale": "private-model-canary-123",
                        "assumptions": ["private-model-canary-123"],
                        "request": {"customers": 20, "cashiers": 2, "seed": 42}
                        if mode == "queue_single_sensitive"
                        else {
                            "requests": 20,
                            "servers": [{"server_id": 1}, {"server_id": 2}],
                            "routing_strategy": "round_robin",
                            "seed": 42,
                        },
                    }
                if mode == "unknown_tool":
                    name = "fabricate_results"
                elif mode == "server_single_sensitive":
                    name = "run_server_simulation"
                elif mode == "queue_single_sensitive":
                    name = "run_simulation"
                elif mode.startswith("server"):
                    name = "compare_server_simulations"
                else:
                    name = "compare_simulations"
                arguments = (
                    "{bad-json"
                    if mode == "invalid_json"
                    else json.dumps(envelope, ensure_ascii=False)
                )
                message: dict[str, Any] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"fixture-{index}",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                }
                if mode in {"text_only", "text_only_sensitive"}:
                    message = {
                        "role": "assistant",
                        "content": "private-model-canary-123 test-secret-not-real"
                        if mode == "text_only_sensitive"
                        else '{"metrics":{"average_wait_time":1}}',
                        "reasoning_content": "hidden-reasoning-canary-456",
                    }
                if mode == "http_error":
                    self.send_response(401)
                    response = {
                        "error": {"message": "test-secret-not-real", "type": "authentication_error"}
                    }
                else:
                    self.send_response(200)
                    response = {
                        "id": f"fixture-{index}",
                        "object": "chat.completion",
                        "created": 0,
                        "model": body["model"],
                        "choices": [
                            {"index": 0, "message": message, "finish_reason": "tool_calls"}
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                try:
                    self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def endpoint() -> Iterator[Callable[..., ModelEndpoint]]:
    created: list[ModelEndpoint] = []

    def factory(responses: list[str], *, timeout_delay: float = 0.5) -> ModelEndpoint:
        server = ModelEndpoint(responses, timeout_delay=timeout_delay)
        created.append(server)
        return server

    yield factory
    for server in created:
        server.close()
