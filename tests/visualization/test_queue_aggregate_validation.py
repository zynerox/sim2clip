"""Queue public loaders must verify aggregates as well as individual event runs."""

import pytest

from sim2clip.visualization.data import VisualizationError, parse_result


def corrupt(official, kind):
    value = official.model_dump(mode="json")
    if kind == "summary":
        for key in ("mean", "minimum", "maximum"):
            value["summaries"][0]["average_wait_time"][key] += 1000
    elif kind == "difference":
        value["paired_differences"][0]["average_wait_time_difference"] += 1
    elif kind == "ranking":
        value["ranking_cashiers"].reverse()
    elif kind == "winner":
        value["winner_cashiers"] = [1]
    elif kind == "references":
        value["summaries"][0]["result_ids"][0] = "0" * 64
    else:
        value["comparison_id"] = "0" * 64
    return value


@pytest.mark.parametrize(
    "kind", ["summary", "difference", "ranking", "winner", "references", "hash"]
)
def test_python_rejects_schema_valid_falsified_queue_comparison(official, kind):
    with pytest.raises(VisualizationError):
        parse_result(corrupt(official, kind))


@pytest.mark.parametrize(
    "kind", ["summary", "difference", "ranking", "winner", "references", "hash"]
)
def test_chromium_rejects_falsified_queue_aggregates(browser, demo_files, official, kind):
    page = browser.new_page()
    from pathlib import Path

    page.goto(Path(demo_files["visualization"]).as_uri())
    error = page.evaluate(
        "v=>{try{Sim2Clip.validateEvidence(v);return null;}catch(e){return e.message;}}",
        corrupt(official, kind),
    )
    assert error
    page.close()
