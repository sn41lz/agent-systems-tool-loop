from __future__ import annotations

import asyncio
import json

import pytest
from jsonschema import Draft202012Validator

from tool_loop.models import ModelRequest, ToolTransientError
from tool_loop.scenarios import get_scenario


def request() -> ModelRequest:
    return ModelRequest(items=(), tools=(), max_output_tokens=512)


@pytest.mark.asyncio
async def test_incident_drill_exercises_parallel_evidence_schema_repair_and_safe_retry() -> None:
    scenario = get_scenario(delay_scale=0)
    tools = {tool.name: tool for tool in scenario.tools}
    initial = await scenario.provider.generate(request())
    assert len(initial.calls) == 4
    valid = []
    invalid = []
    for call in initial.calls:
        args = json.loads(call.arguments)
        errors = list(Draft202012Validator(tools[call.name].parameters).iter_errors(args))
        (invalid if errors else valid).append(call)
    assert {call.name for call in valid} == {"inspect_service", "recent_deploys", "compare_metrics"}
    assert [call.name for call in invalid] == ["query_logs"]
    assert all(tools[call.name].parallel_safe for call in valid)
    evidence = await asyncio.gather(
        *(tools[call.name].handler(json.loads(call.arguments)) for call in valid)
    )
    assert all(item["synthetic"] for item in evidence)
    assert {item["evidence_id"] for item in evidence} == {"topology-01", "deploy-02", "metrics-03"}

    correction = await scenario.provider.generate(request())
    assert len(correction.calls) == 1
    corrected = correction.calls[0]
    logs = tools[corrected.name]
    arguments = json.loads(corrected.arguments)
    Draft202012Validator(logs.parameters).validate(arguments)
    assert logs.retry_safe is True
    assert logs.max_attempts == 2
    with pytest.raises(ToolTransientError):
        await logs.handler(arguments)
    assert (await logs.handler(arguments))["evidence_id"] == "logs-04"

    final = await scenario.provider.generate(request())
    assert final.calls == ()
    assert all(
        evidence in final.text for evidence in ("topology-01", "deploy-02", "metrics-03", "logs-04")
    )
    assert "No remediation was executed" in final.text
    assert "not a proven cause" in final.text
    assert "synthetic" in final.text.lower()
    assert "read-only" in scenario.prompt


@pytest.mark.asyncio
async def test_each_scenario_has_fresh_tool_failure_state_and_evidence() -> None:
    args = {"service": "checkout-api", "region": "eu-west-1", "window_minutes": 5}
    for _ in range(2):
        scenario = get_scenario(delay_scale=0)
        logs = next(tool for tool in scenario.tools if tool.name == "query_logs")
        with pytest.raises(ToolTransientError):
            await logs.handler(args)
        first = await logs.handler(args)
        first["entries"].clear()
        assert (await logs.handler(args))["entries"]


@pytest.mark.asyncio
async def test_loop_drill_repeats_semantics_with_distinct_call_ids() -> None:
    scenario = get_scenario("loop", delay_scale=0)
    first = (await scenario.provider.generate(request())).calls[0]
    second = (await scenario.provider.generate(request())).calls[0]
    assert first.call_id != second.call_id
    assert (first.name, first.arguments) == (second.name, second.arguments)


@pytest.mark.parametrize("scale", [-1, float("inf"), float("nan")])
def test_invalid_delay_scale_is_rejected(scale: float) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        get_scenario(delay_scale=scale)


def test_unknown_scenario_is_actionable() -> None:
    with pytest.raises(ValueError, match="Choose 'incident' or 'loop'"):
        get_scenario("not-a-scenario")
