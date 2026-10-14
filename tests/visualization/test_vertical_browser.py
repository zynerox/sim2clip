"""Actual Chromium correctness and composition checks for both Phase 7.3 stages."""

from pathlib import Path
from typing import Any

import pytest

from sim2clip.scenarios.server_contracts import (
    RequestSample,
    ServerComparisonRequest,
    ServerSimulationConfig,
)
from sim2clip.simulation import SimulationConfig, run_comparison, run_simulation
from sim2clip.simulation.schemas import CustomerSample
from sim2clip.simulation.server_comparison import run_server_comparison
from sim2clip.simulation.server_model import run_server_simulation
from sim2clip.visualization.html import render_html
from tests.visualization.test_server_browser import comparison


@pytest.fixture(params=["queue", "server"])
def vertical_page(request: Any, browser: Any, tmp_path: Path) -> Any:
    result = (
        run_comparison(
            tuple(SimulationConfig(customers=40, cashiers=c, seed=7) for c in (1, 3, 8)),
            seeds=(7, 8),
        )
        if request.param == "queue"
        else comparison(count=40, groups=3, axis="server_pool")
    )
    path = tmp_path / "vertical.html"
    path.write_text(render_html(result), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1440, "height": 2300})
    errors = []
    requests = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda item: requests.append(item.url))
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.ready || null")
    assert not page.locator("#error").is_visible()
    page.locator("#layout").select_option("vertical")
    yield page
    assert not errors
    assert all(url.startswith("file:") for url in requests)
    page.close()


def assert_accounting(page: Any) -> None:
    proof = page.evaluate("""() => ({poses:Sim2Clip.getStagePoses(),
      states:Sim2Clip.getState().snapshots,server:!!Sim2Clip.scenario_id,
      totals:Sim2Clip.getState().selected.map(id=>{
        const r=Sim2Clip.getResult(),all=r.results?.map(x=>x.result||x);
        const run=all?.find(x=>x.result_id===id)||r;
        return run.config.requests||run.config.customers;
      })})""")
    for pose, state, total in zip(proof["poses"], proof["states"], proof["totals"], strict=True):
        assert pose["timestamp"] == state["timestamp"]
        assert (
            pose["not_arrived"] + pose["waiting"] + pose["processing"] + pose["completed"] == total
        )
        if proof["server"]:
            assert [pose[k] for k in ("waiting", "processing", "completed")] == [
                state[k] for k in ("waiting", "processing", "completed")
            ]
            assert pose["waiting_visible"] + pose["waiting_hidden"] == state["waiting"]
            assert (
                sum(s["queue_count"] for s in pose["servers"] + pose["hidden_servers"])
                == state["waiting"]
            )
            assert all(
                s["queue_visible"] + s["queue_hidden"] == s["queue_count"] for s in pose["servers"]
            )
            key = "request_id"
            height = pose["height"]
            for entity in pose["entities"]:
                assert 18 <= entity["x"] <= 858 and 18 <= entity["y"] <= height - 18
                assert state["request_states"][str(entity["request_id"])] == entity["status"]
                if entity["status"] == "processing":
                    assert state["active"][str(entity["server_id"])] == entity["request_id"]
                else:
                    assert entity["request_id"] in state["queues"][str(entity["server_id"])]
            groups = pose["token_groups"]
            drawn = [e["request_id"] for g in groups for e in g["entities"]]
            assert len(drawn) == len(set(drawn))
            assert set(drawn) == {
                e["request_id"]
                for e in pose["entities"]
                if e["status"] != "processing" or e["moving"]
            }
            for i, group in enumerate(groups):
                assert all(
                    abs(group["x"] - other["x"]) >= 38 or abs(group["y"] - other["y"]) >= 38
                    for other in groups[i + 1 :]
                )
        else:
            assert pose["waiting"] == len(state["queue"])
            assert pose["processing"] == sum(c is not None for c in state["cashiers"])
            assert pose["completed"] == state["completed"]
            assert pose["queue_visible"] + pose["queue_hidden"] == len(state["queue"])
            assert pose["serving_visible"] + pose["serving_hidden"] == pose["processing"]
            key = "customer_id"
            for i, entity in enumerate(pose["entities"]):
                assert 20 <= entity["x"] <= 856 and 64 <= entity["y"]
                assert state["customer_states"][str(entity["customer_id"])] == entity["status"]
                assert all(
                    abs(entity["x"] - other["x"]) >= 38 - 1e-7
                    or abs(entity["y"] - other["y"]) >= 66 - 1e-7
                    for other in pose["entities"][i + 1 :]
                )
        assert len({e[key] for e in pose["entities"]}) == len(pose["entities"])


def test_layout_toggle_preserves_data_selection_clock_and_playing(vertical_page: Any) -> None:
    page = vertical_page
    page.evaluate("Sim2Clip.seek(Sim2Clip.getState().endTime*.4)")
    state, poses = page.evaluate("[Sim2Clip.getState(),Sim2Clip.getStagePoses()]")
    page.locator("#layout").select_option("desktop")
    assert page.locator("#panels").is_visible()
    assert not page.locator("#vertical-preview").is_visible()
    assert page.locator("canvas").count() == len(state["selected"])
    page.locator("#layout").select_option("vertical")
    assert page.evaluate("Sim2Clip.getState()") == state
    assert page.evaluate("Sim2Clip.getStagePoses()") == poses
    assert page.evaluate("""()=>{Sim2Clip.play();const s=JSON.stringify(Sim2Clip.getState());
      Sim2Clip.setLayout('desktop');Sim2Clip.setLayout('vertical');
      const same=s===JSON.stringify(Sim2Clip.getState());Sim2Clip.pause();return same;}""")


@pytest.mark.parametrize("width", [360, 390, 1440])
def test_native_stage_phone_bounds_safe_areas_and_no_scroll(vertical_page: Any, width: int) -> None:
    page = vertical_page
    page.set_viewport_size({"width": width, "height": 2300 if width == 1440 else 844})
    if width == 1440:
        page.evaluate("document.getElementById('vertical-preview').style.width='1080px'")
    page.wait_for_function("""() => {const p=document.getElementById('vertical-preview');
      return Math.abs(p.getBoundingClientRect().height/p.clientWidth-1920/1080)<.002;}""")
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    dimensions = page.evaluate("""() => {
      const stage=document.getElementById('vertical-stage');
      const selector='.stage-brand,.stage-type,.stage-question,.stage-conditions';
      const nodes=[...stage.querySelectorAll(selector+',.stage-card,.stage-footer')];
      return {width:stage.offsetWidth,height:stage.offsetHeight,
        bounds:nodes.map(n=>({right:n.offsetLeft+n.offsetWidth,bottom:n.offsetTop+n.offsetHeight})),
        overflow:[...stage.querySelectorAll('.stage-question,.stage-conditions,.stage-name,.stage-config,.stage-stat')]
          .filter(n=>n.scrollWidth>n.clientWidth+1||n.scrollHeight>n.clientHeight+1).map(n=>n.className),
        controls:stage.querySelectorAll('input,select,button').length};
    }""")
    assert (dimensions["width"], dimensions["height"]) == (1080, 1920)
    assert not dimensions["overflow"]
    assert not dimensions["controls"]
    assert all(b["right"] <= 936 and b["bottom"] <= 1730 for b in dimensions["bounds"])


def test_nonmonotonic_seek_aggregation_and_finished_group_hold(vertical_page: Any) -> None:
    page = vertical_page
    end = page.evaluate("Sim2Clip.getState().endTime")
    for t in (0, end * 0.01, end * 0.4, end, end * 0.2, 0, end * 0.5):
        page.evaluate("t=>Sim2Clip.seek(t)", t)
        assert_accounting(page)
    poses = page.evaluate("Sim2Clip.getStagePoses()")
    page.evaluate("Sim2Clip.seek(0)")
    page.evaluate("t=>{for(let i=0;i<100;i++)Sim2Clip.seek(t*i/100);Sim2Clip.seek(t)}", end * 0.5)
    assert page.evaluate("Sim2Clip.getStagePoses()") == poses
    page.evaluate("Sim2Clip.seek(Sim2Clip.getState().endTime)")
    assert page.locator(".stage-status").all_text_contents() == ["全部完成", "全部完成"]


def test_actual_play_pause_direct_seek_and_speed(vertical_page: Any) -> None:
    page = vertical_page
    page.locator("#speed").select_option("2")
    page.locator("#play").click()
    page.wait_for_function("Sim2Clip.getState().clock>Sim2Clip.getState().endTime*.02")
    page.locator("#play").click()
    state = page.evaluate("Sim2Clip.getState()")
    pose = page.evaluate("Sim2Clip.getStagePoses()")
    png = page.locator(".stage-canvas").first.screenshot()
    page.evaluate("Sim2Clip.seek(0)")
    page.evaluate("t=>Sim2Clip.seek(t)", state["clock"])
    assert page.evaluate("Sim2Clip.getStagePoses()") == pose
    assert page.locator(".stage-canvas").first.screenshot() == png
    page.locator("#scrubber").fill("700")
    page.locator("#scrubber").dispatch_event("input")
    assert page.evaluate("Sim2Clip.getState().clock") == pytest.approx(state["endTime"] * 0.7)
    page.locator("#scrubber").fill("200")
    page.locator("#scrubber").dispatch_event("input")
    assert_accounting(page)


def test_seed_candidate_and_file_load_refresh_real_title(
    vertical_page: Any, tmp_path: Path
) -> None:
    page = vertical_page
    is_server = page.evaluate("!!Sim2Clip.scenario_id")
    page.locator("#seed").select_option("43" if is_server else "8")
    page.locator("#candidate").select_option(index=1)
    assert page.evaluate("Sim2Clip.getLayout()") == "vertical"
    assert page.evaluate("Sim2Clip.getState().clock") == 0
    assert_accounting(page)
    if not is_server:
        assert "8 个" in page.locator(".stage-question").inner_text()
    result = (
        run_server_simulation(ServerSimulationConfig(requests=20, seed=9))
        if is_server
        else run_simulation(SimulationConfig(customers=20, cashiers=2, seed=9))
    )
    path = tmp_path / "single.json"
    path.write_text(result.model_dump_json(), encoding="utf-8")
    page.locator("#load-file").set_input_files(str(path))
    page.wait_for_function("Sim2Clip.getResult()?.config?.seed===9")
    assert page.locator(".stage-card").count() == 1
    assert "20" in page.locator(".stage-question").inner_text()
    assert "单组" in page.locator(".stage-type").inner_text()
    assert_accounting(page)


@pytest.mark.parametrize("count,cashiers", [(120, 1), (120, 3), (120, 8), (120, 100), (10000, 100)])
def test_queue_extreme_slots_collision_and_exact_aggregation(
    browser: Any, tmp_path: Path, count: int, cashiers: int
) -> None:
    run = run_simulation(
        SimulationConfig(customers=count, cashiers=cashiers),
        tuple(CustomerSample(customer_id=i, arrival_time=0, service_time=10) for i in range(count)),
    )
    path = tmp_path / "queue.html"
    path.write_text(render_html(run), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.setLayout('vertical')")
    for t in (0, 1.5, 5, 10, 10.1, 12, run.metrics.simulation_end_time, 0):
        page.evaluate("t=>Sim2Clip.seek(t)", t)
        assert_accounting(page)
    page.close()


@pytest.mark.parametrize("kind", ["queue", "server"])
def test_long_ids_and_large_times_fit_without_changing_states(
    browser: Any, tmp_path: Path, kind: str
) -> None:
    rid = 2**31 - 1
    run = (
        run_simulation(
            SimulationConfig(customers=1, cashiers=1),
            (CustomerSample(customer_id=rid, arrival_time=0, service_time=1e9),),
        )
        if kind == "queue"
        else run_server_simulation(
            ServerSimulationConfig(
                requests=1, servers=({"server_id": rid, "processing_rate": 0.001},)
            ),
            (RequestSample(request_id=rid, arrival_time=0, work=1e6),),
        )
    )
    path = tmp_path / "large-time.html"
    path.write_text(render_html(run), encoding="utf-8")
    page = browser.new_page(viewport={"width": 360, "height": 844})
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.ready || null")
    page.evaluate("Sim2Clip.setLayout('vertical');Sim2Clip.seek(5e8)")
    assert_accounting(page)
    assert "e+8" in page.locator(".stage-clock").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    assert page.evaluate("""()=>{const c=document.querySelector('.stage-clock');
      return c.scrollWidth===c.clientWidth;}""")
    assert page.evaluate("Sim2Clip.getState().clock") == 5e8
    page.close()


def test_rr_lo_same_pool_noncontinuous_ids_and_actual_rates(browser: Any, tmp_path: Path) -> None:
    result = comparison(count=100, heterogeneous=True)
    path = tmp_path / "strategy.html"
    path.write_text(render_html(result), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.ready")
    page.evaluate("Sim2Clip.setLayout('vertical')")
    assert "分流策略对比" in page.locator(".stage-type").inner_text()
    assert "RR 与 LO" in page.locator(".stage-question").inner_text()
    assert page.locator(".stage-config").all_text_contents() == [
        "RR 轮换 · 每台独立 FIFO",
        "LO 最少在途 · 每台独立 FIFO",
    ]
    page.evaluate("Sim2Clip.seek(Sim2Clip.getState().endTime*.1)")
    assert_accounting(page)
    for pose in page.evaluate("Sim2Clip.getStagePoses()"):
        assert [(s["server_id"], s["rate"]) for s in pose["servers"]] == [(3, 1), (8, 2)]
    # A legal pool comparison can change IDs without changing counts or rates.
    pool = run_server_comparison(
        ServerComparisonRequest(
            comparison_axis="server_pool",
            seeds=(42,),
            experiments=tuple(
                {
                    "group_id": f"pool_{i}",
                    "config": ServerSimulationConfig(
                        requests=20, servers=tuple({"server_id": sid} for sid in ids)
                    ),
                }
                for i, ids in enumerate(((1, 2), (3, 8)))
            ),
        )
    )
    # File JSON preserves +0.0; Playwright object transport serializes Python zero as -0.
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(pool.model_dump_json(), encoding="utf-8")
    page.locator("#load-file").set_input_files(str(pool_path))
    page.wait_for_function("id=>Sim2Clip.getResult()?.comparison_id===id", arg=pool.comparison_id)
    assert not page.locator("#error").is_visible()
    assert "不同服务器池" in page.locator(".stage-question").inner_text()
    assert "不同处理速率" not in page.locator(".stage-question").inner_text()
    assert_accounting(page)
    page.close()


@pytest.mark.parametrize("servers", [1, 3, 32])
def test_server_large_counts_rates_and_hidden_nodes(
    browser: Any, tmp_path: Path, servers: int
) -> None:
    run = run_server_simulation(
        ServerSimulationConfig(
            requests=10000,
            arrivals={"mode": "batch"},
            workload={"mode": "constant", "work": 1.0},
            servers=tuple(
                {"server_id": i + 1, "processing_rate": 1 if i % 2 == 0 else 2}
                for i in range(servers)
            ),
        )
    )
    path = tmp_path / "server.html"
    path.write_text(render_html(run), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.ready")
    page.evaluate("Sim2Clip.setLayout('vertical')")
    for t in (0, 0.12, 1, run.metrics.simulation_end_time, 0):
        page.evaluate("t=>Sim2Clip.seek(t)", t)
        assert_accounting(page)
    assert page.evaluate("Sim2Clip.getStagePoses()[0].hidden_servers.length") == max(0, servers - 3)
    page.close()
