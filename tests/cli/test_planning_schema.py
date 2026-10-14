import copy

from sim2clip.agent.planner import planning_tools
from sim2clip.mcp_server.simulation_server import _tool_definitions


def test_planning_envelope_reuses_each_mcp_schema_and_preserves_definitions() -> None:
    tools = _tool_definitions()
    originals = copy.deepcopy(tools)
    for tool, definition in zip(tools, planning_tools(tools), strict=True):
        schema = definition["parameters"]
        request = copy.deepcopy(schema["properties"]["request"])
        if "$defs" in schema:
            request["$defs"] = schema["$defs"]
        assert request == tool.input_schema
        assert set(schema["required"]) == {"experiment_goal", "rationale", "assumptions", "request"}
        assert schema["additionalProperties"] is False
    assert tools == originals
