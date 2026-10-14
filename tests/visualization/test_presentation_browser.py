"""Actual offline Chromium, including canvas pixels and narrative isolation."""

from pathlib import Path
from typing import Any

import pytest

from sim2clip.simulation import SimulationConfig, run_comparison, run_simulation
from sim2clip.visualization.html import render_html
from sim2clip.visualization.presentation import PresentationConfig, build_manifest
from tests.visualization.test_server_browser import comparison
from tests.visualization.test_vertical_browser import assert_accounting


@pytest.fixture(params=["queue", "server"])
def narrative_page(request: Any, browser: Any, tmp_path: Path):
    result = (
        run_comparison(
            tuple(SimulationConfig(customers=40, cashiers=c) for c in (1, 3, 8)), seeds=(42, 43)
        )
        if request.param == "queue"
        else comparison(count=40, groups=3, axis="server_pool")
    )
    path = tmp_path / "presentation.html"
    path.write_text(
        render_html(result, context={"report": "FINAL_REPORT_CANARY"}), encoding="utf-8"
    )
    page = browser.new_page(viewport={"width": 390, "height": 844})
    errors, requests = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.ready || null")
    page.evaluate("Sim2Clip.setLayout('vertical');Sim2Clip.setMode('presentation')")
    yield page
    assert not errors
    assert all(url.startswith("file:") for url in requests)
    page.close()


@pytest.mark.parametrize(
    "p,phase",
    [
        (0, "question"),
        (3, "plan"),
        (6, "tool"),
        (8, "verify"),
        (10, "simulate"),
        (17, "simulate"),
        (24, "reveal"),
        (30, "reveal"),
    ],
)
def test_boundaries_isolation_and_exact_states(narrative_page, p, phase):
    page = narrative_page
    page.evaluate("p=>Sim2Clip.seekPresentation(p)", p)
    s = page.evaluate("Sim2Clip.getPresentationState()")
    assert s["phase"] == phase
    assert s["presentation_time"] == p
    end = page.evaluate("Sim2Clip.getState().endTime")
    assert s["simulation_time"] == pytest.approx(end * max(0, min(1, (p - 10) / 14)))
    assert s["reveal_visible"] == (phase == "reveal")
    assert not page.locator(
        "#report,#timeline,#summary-table,#panels,.panel-foot,.server-metrics"
    ).count()
    assert "FINAL_REPORT_CANARY" not in page.locator("body").inner_text()
    assert "全部完成" not in page.locator("#end-time").inner_text()
    assert page.locator(".p-results").count() == (phase == "reveal")
    assert page.locator("canvas").count() == (2 if phase == "simulate" else 0)
    assert_accounting(page)
    if phase == "reveal":
        assert "本次回放 · Seed" in page.locator(".p-scope").inner_text()
        assert "配对 Seed 的统计汇总" in page.locator(".p-note").last.inner_text()
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")


def test_seek_reverse_pixels_pause_modes_layout_and_selections(narrative_page):
    page = narrative_page
    page.evaluate("Sim2Clip.seekPresentation(17)")
    expected = page.evaluate("[Sim2Clip.getState(),Sim2Clip.getStagePoses()]")
    pixels = page.locator(".stage-canvas").first.screenshot()
    page.evaluate("Sim2Clip.seekPresentation(30);Sim2Clip.seekPresentation(17)")
    assert page.evaluate("[Sim2Clip.getState(),Sim2Clip.getStagePoses()]") == expected
    assert page.locator(".stage-canvas").first.screenshot() == pixels
    page.evaluate(
        "()=>{for(let p=0;p<17;p+=.1)Sim2Clip.seekPresentation(p);Sim2Clip.seekPresentation(17)}"
    )
    assert page.locator(".stage-canvas").first.screenshot() == pixels
    page.evaluate("Sim2Clip.setLayout('desktop')")
    assert page.evaluate("Sim2Clip.getState()") == expected[0]
    page.evaluate("Sim2Clip.setLayout('vertical');Sim2Clip.setMode('analysis')")
    assert page.locator("#report").count() == 1
    assert page.evaluate("Sim2Clip.getState()") == expected[0]
    page.evaluate("Sim2Clip.setMode('presentation')")
    assert page.evaluate("Sim2Clip.getPresentationState().presentation_time") == pytest.approx(17)
    page.locator("#seed").select_option("43")
    assert page.evaluate("Sim2Clip.getPresentationState().phase") == "question"
    page.locator("#candidate").select_option(index=1)
    page.evaluate("Sim2Clip.seekPresentation(17)")
    assert len(page.evaluate("Sim2Clip.getState().selected")) == 2
    assert_accounting(page)
    page.locator("#restart").click()
    assert page.evaluate("Sim2Clip.getPresentationState().presentation_time") == 0
    page.locator("#scrubber").fill("900")
    page.locator("#scrubber").dispatch_event("input")
    assert page.evaluate("Sim2Clip.getPresentationState().phase") == "reveal"
    page.evaluate("Sim2Clip.seek(Sim2Clip.getState().endTime*.2)")
    assert not page.locator(".p-results").count()


def test_actual_play_pause_and_continues_past_simulation_end(narrative_page):
    page = narrative_page
    page.evaluate("Sim2Clip.seekPresentation(23.9);Sim2Clip.play()")
    page.wait_for_function("Sim2Clip.getPresentationState().presentation_time>24.3")
    assert page.evaluate("Sim2Clip.getState().playing")
    assert page.evaluate("Sim2Clip.getState().clock===Sim2Clip.getState().endTime")
    assert page.evaluate(
        """()=>{const s=JSON.stringify(Sim2Clip.getState());Sim2Clip.setLayout('desktop');
        Sim2Clip.setLayout('vertical');Sim2Clip.setMode('analysis');
        Sim2Clip.setMode('presentation');return s===JSON.stringify(Sim2Clip.getState())}"""
    )
    page.evaluate("Sim2Clip.pause()")
    frozen = page.evaluate("Sim2Clip.getPresentationState()")
    page.wait_for_timeout(180)
    assert page.evaluate("Sim2Clip.getPresentationState()") == frozen
    page.evaluate("Sim2Clip.seekPresentation(29.9);Sim2Clip.play()")
    page.wait_for_function("!Sim2Clip.getState().playing")
    assert page.evaluate("Sim2Clip.getPresentationState().presentation_time") == 30


@pytest.mark.parametrize("width", [360, 390])
def test_phone_all_phases_fit(narrative_page, width):
    page = narrative_page
    page.set_viewport_size({"width": width, "height": 844})
    for p in (0, 3, 6, 8, 17, 25):
        page.evaluate("p=>Sim2Clip.seekPresentation(p)", p)
        page.wait_for_function(
            "Math.abs(document.querySelector('.presentation-preview').getBoundingClientRect().height/innerWidth-1920/1080)<.01"
        )
        overflow = page.evaluate(
            """() => [...document.querySelectorAll(
            '.p-question,.p-conditions,.p-info,.p-body,.p-result,.p-footer')]
            .filter(n=>n.scrollHeight>n.clientHeight+1||n.scrollWidth>n.clientWidth+1)
            .map(n=>({class:n.className,h:n.clientHeight,sh:n.scrollHeight}))"""
        )
        assert not overflow
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")


def test_single_zero_baseline_and_file_load(narrative_page, tmp_path):
    page = narrative_page
    server = page.evaluate("!!Sim2Clip.scenario_id")
    if server:
        from sim2clip.scenarios.server_contracts import ServerSimulationConfig
        from sim2clip.simulation.server_model import run_server_simulation

        result = run_server_simulation(ServerSimulationConfig(requests=5, seed=9))
    else:
        result = run_simulation(SimulationConfig(customers=5, cashiers=2, seed=9))
    path = tmp_path / "new.json"
    path.write_text(result.model_dump_json())
    page.locator("#load-file").set_input_files(str(path))
    page.wait_for_function("Sim2Clip.getResult()?.config?.seed===9")
    assert page.evaluate("Sim2Clip.getPresentationState().presentation_time") == 0
    page.evaluate("Sim2Clip.seekPresentation(25)")
    assert page.locator(".p-result").count() == 1
    assert not page.locator(".p-delta").count()
    page.evaluate("Sim2Clip.seekPresentation(17)")
    assert page.locator("canvas").count() == 1
    assert_accounting(page)


def test_custom_configuration_offline_default_mode(browser, tmp_path):
    result = run_simulation(SimulationConfig(customers=5, cashiers=1))
    config = PresentationConfig(
        duration_seconds=60, phase_durations=(6, 6, 4, 4, 28, 12), default_mode="presentation"
    )
    path = tmp_path / "custom.html"
    path.write_text(
        render_html(result, presentation=build_manifest(result, config=config)), encoding="utf-8"
    )
    page = browser.new_page()
    page.goto(path.as_uri())
    assert page.evaluate("Sim2Clip.getPresentationState().mode") == "presentation"
    page.evaluate("Sim2Clip.seekPresentation(34)")
    assert page.evaluate("Sim2Clip.getState().clock") == pytest.approx(
        result.metrics.simulation_end_time / 2
    )
    page.close()


@pytest.mark.parametrize("kind", ["cached", "failed", "missing", "agent_failure"])
def test_public_execution_states_do_not_invent_history(browser, tmp_path, kind):
    from tests.visualization.test_presentation import evidence_state

    result = run_simulation(SimulationConfig(customers=5, cashiers=1))
    store, state, rows = evidence_state(result, tmp_path / "artifacts")
    state["messages"] = ["SENSITIVE_AIMessage_reason"]
    state["user_query"] = "SENSITIVE_AIMessage_reason"
    if kind == "cached":
        state["tool_calls"][0]["cached"] = True
    elif kind == "failed":
        state["tool_calls"][0]["status"] = "error"
        state["tool_calls"][0]["error"] = "SENSITIVE_AIMessage_reason"
    elif kind == "missing":
        state["tool_calls"][0].pop("status")
        state["tool_calls"][0].pop("cached")
    else:
        state["validation_result"]["valid"] = False
    manifest = build_manifest(result, state=state, store=store, timeline=rows)
    path = tmp_path / "status.html"
    path.write_text(render_html(result, presentation=manifest), encoding="utf-8")
    assert "SENSITIVE_AIMessage_reason" not in path.read_text(encoding="utf-8")
    page = browser.new_page(viewport={"width": 360, "height": 844})
    page.goto(path.as_uri())
    page.evaluate(
        "Sim2Clip.setMode('presentation');Sim2Clip.setLayout('vertical');Sim2Clip.seekPresentation(7)"
    )
    text = page.locator(".p-body").inner_text()
    expected = {
        "cached": "已复用缓存",
        "failed": "工具调用失败",
        "missing": "工具状态未记录",
        "agent_failure": "工具调用成功",
    }
    assert expected[kind] in text
    if kind in {"failed", "missing"}:
        assert "关联尚未核实" in text
    page.evaluate("Sim2Clip.seekPresentation(25)")
    assert page.locator(".p-results").count() == (0 if kind == "agent_failure" else 1)
    page.close()


def test_zero_denominator_tie_uses_selected_seed(browser, tmp_path):
    result = run_comparison(
        tuple(SimulationConfig(customers=1, cashiers=c) for c in (1, 3)), seeds=(42, 43)
    )
    path = tmp_path / "zero.html"
    path.write_text(render_html(result), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.setMode('presentation');Sim2Clip.seekPresentation(25)")
    text = page.locator("#presentation-preview").inner_text()
    assert "容差内持平" in text
    assert "不可计算（A = 0）" in text
    assert "Infinity" not in text
    page.close()


def test_rr_lo_heterogeneous_legal_metric_and_no_preset_winner(browser, tmp_path):
    from sim2clip.agent.artifacts import ArtifactStore
    from tests.visualization.test_presentation import evidence_state

    result = comparison(count=40, heterogeneous=True)
    # A receipt-backed comparison retains a legal locked P95 goal.
    name = "compare_server_simulations"
    call = {
        "id": "r1-c0",
        "name": name,
        "arguments": result.request.model_dump(mode="json"),
        "status": "success",
        "round": 1,
        "attempt": 1,
        "cached": False,
    }
    store = ArtifactStore(tmp_path / "receipts")
    call["response"] = store.save_response("test", call, result.model_dump(mode="json"))
    _, state, rows = evidence_state(result.results[0].result, tmp_path / "unused")
    state["tool_calls"] = [call]
    rows[1]["tool_calls"] = [call]
    state["design"] = {"primary_metric": "p95_response_time"}
    manifest = build_manifest(result, state=state, store=store, timeline=rows)
    path = tmp_path / "rr-lo.html"
    path.write_text(render_html(result, presentation=manifest), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.ready")
    page.evaluate("Sim2Clip.setMode('presentation');Sim2Clip.seekPresentation(4)")
    assert "P95 响应时间" in page.locator(".p-body").inner_text()
    assert "服务器池" in page.locator(".p-body").inner_text()
    page.evaluate("Sim2Clip.seekPresentation(25)")
    assert page.locator(".p-primary small").all_text_contents() == ["P95 响应时间"] * 2
    values = page.evaluate(
        """()=>{const r=Sim2Clip.getResult();
        return r.results.slice(0,2).map(x=>x.result.metrics.p95_response_time)}"""
    )
    delta = values[1] - values[0]
    assert ("更低" if delta < 0 else "更高") in page.locator(".p-delta").inner_text()
    page.close()


def test_mode_roundtrip_preserves_opening_progress_and_failure_clears_server(
    narrative_page, tmp_path
):
    page = narrative_page
    page.evaluate(
        "Sim2Clip.seekPresentation(8.5);Sim2Clip.setMode('analysis');Sim2Clip.setMode('presentation')"
    )
    assert page.evaluate("Sim2Clip.getPresentationState().presentation_time") == 8.5
    page.evaluate("Sim2Clip.setMode('analysis')")
    page.locator("#seed").select_option("43")
    page.locator("#seed").select_option("42")
    page.evaluate("Sim2Clip.setMode('presentation')")
    assert page.evaluate("Sim2Clip.getPresentationState().presentation_time") == 0
    if page.evaluate("!!Sim2Clip.scenario_id"):
        path = tmp_path / "bad.json"
        path.write_text("{}", encoding="utf-8")
        page.locator("#load-file").set_input_files(str(path))
        page.wait_for_function("!document.getElementById('error').hidden")
        assert not page.locator(".p-result,canvas").count()
        assert "等待已验证" in page.locator(".p-question").inner_text()


def test_public_question_does_not_describe_previous_selected_group(browser, tmp_path):
    result = run_comparison(
        tuple(SimulationConfig(customers=5, cashiers=c) for c in (1, 3, 8)), seeds=(42,)
    )
    manifest = build_manifest(result, public_question="公开原始问题：1 个与 3 个收银台？")
    path = tmp_path / "public-question.html"
    path.write_text(render_html(result, presentation=manifest), encoding="utf-8")
    page = browser.new_page()
    page.goto(path.as_uri())
    page.evaluate("Sim2Clip.setMode('presentation')")
    assert "公开原始问题" in page.locator(".p-question").inner_text()
    page.locator("#candidate").select_option("8")
    assert "公开原始问题" not in page.locator(".p-question").inner_text()
    assert "8 个" in page.locator(".p-question").inner_text()
    page.close()
