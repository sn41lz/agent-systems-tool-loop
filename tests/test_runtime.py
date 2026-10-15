"""Behavioral checks for the runtime's trust, scheduling, and budget boundaries."""

import asyncio
import json
from dataclasses import replace

import pytest

from tool_loop.models import (
    Limits,
    ModelReply,
    Pricing,
    ProviderError,
    ProviderTransientError,
    Tool,
    ToolCall,
    ToolTransientError,
    Usage,
)
from tool_loop.providers import FixtureProvider
from tool_loop.runtime import Agent
from tool_loop.scenarios import get_scenario
from tool_loop.telemetry import Telemetry

SCHEMA = {
    "type": "object",
    "properties": {"n": {"type": "integer"}},
    "required": ["n"],
    "additionalProperties": False,
}


def call(call_id="a", name="read", arguments='{"n":1}'):
    return ToolCall(call_id, name, arguments)


async def echo(args):
    return args["n"]


def tool(handler=echo, **kwargs):
    return Tool("read", "Read a number", SCHEMA, handler, **kwargs)


def provider(*replies):
    return FixtureProvider(list(replies))


async def run_script(calls, tools=None, **limits):
    model = provider(ModelReply(calls=tuple(calls)), ModelReply(text="finished"))
    runtime = Agent(model, tools or [tool()], limits=Limits(**limits))
    return await runtime.run("test"), model


@pytest.mark.asyncio
async def test_incident_recovers_schema_and_transient_failure():
    scenario = get_scenario(delay_scale=0)
    result = await Agent(scenario.provider, scenario.tools).run(scenario.prompt)
    assert result.status == "completed"
    assert result.turns == 3 and result.tool_calls == 5
    assert [item.ok for item in result.tool_results] == [True, True, True, False, True]
    assert result.tool_results[3].error["code"] == "schema_validation"
    assert result.tool_results[3].attempts == 0
    assert result.tool_results[4].attempts == 2
    assert result.usage.total_tokens == 3430
    assert "deploy-02" in result.final_text
    assert result.cost_usd is None


@pytest.mark.asyncio
async def test_actual_tool_attempts_each_have_child_spans_with_retry_outcomes():
    scenario = get_scenario(delay_scale=0)
    with Telemetry() as telemetry:
        await Agent(scenario.provider, scenario.tools, telemetry=telemetry).run(scenario.prompt)
    parent = next(
        span
        for span in telemetry.events
        if span["name"] == "tool.execute" and span["attributes"]["tool.call_id"] == "logs-repaired"
    )
    attempts = [
        span
        for span in telemetry.events
        if span["name"] == "tool.attempt" and span["parent_span_id"] == parent["span_id"]
    ]
    assert [span["attributes"]["agent.attempt"] for span in attempts] == [1, 2]
    assert [span["status"] for span in attempts] == ["ERROR", "OK"]
    assert not any(
        span["name"] == "tool.attempt" and span["attributes"]["tool.call_id"] == "logs-invalid"
        for span in telemetry.events
    )


@pytest.mark.asyncio
async def test_parallel_reads_overlap_but_reduction_keeps_proposal_order():
    entered = asyncio.Event()
    active = peak = 0

    async def read(args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            entered.set()
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.005 if args["n"] == 1 else 0)
        active -= 1
        return args["n"]

    result, model = await run_script(
        [call("first", arguments='{"n":1}'), call("second", arguments='{"n":2}')],
        [tool(read, parallel_safe=True)],
        max_parallel=2,
    )
    assert peak == 2
    assert [item.value for item in result.tool_results] == [1, 2]
    assert [
        item["call_id"]
        for item in model.requests[1].items
        if item.get("type") == "function_call_output"
    ] == ["first", "second"]


@pytest.mark.asyncio
async def test_semaphore_bounds_all_read_execution():
    active = peak = 0

    async def read(args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.002)
        active -= 1
        return args["n"]

    result, _ = await run_script(
        [call(str(i)) for i in range(12)], [tool(read, parallel_safe=True)], max_parallel=3
    )
    assert result.status == "completed" and peak == 3 and active == 0


@pytest.mark.asyncio
async def test_side_effect_tools_are_serial_barriers_between_read_groups():
    order = []

    async def read(args):
        order.append(f"read-{args['n']}")
        await asyncio.sleep(0)
        order.append(f"done-{args['n']}")
        return args["n"]

    async def write(_args):
        assert "done-1" in order and "done-2" in order
        assert "read-3" not in order
        order.append("write")
        return "ok"

    result, _ = await run_script(
        [
            call("a"),
            call("b", arguments='{"n":2}'),
            call("c", "write"),
            call("d", arguments='{"n":3}'),
        ],
        [tool(read, parallel_safe=True), Tool("write", "Write", SCHEMA, write)],
    )
    assert result.status == "completed"
    assert order.index("write") < order.index("read-3")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments,code",
    [
        ('{"n":"1"}', "schema_validation"),
        ('{"n":1,"extra":true}', "schema_validation"),
        ("[1]", "schema_validation"),
        ('{"n":1,"n":2}', "invalid_json"),
        ('{"n":NaN}', "invalid_json"),
        ('{"n":1e999}', "invalid_json"),
        ("not-json", "invalid_json"),
        ('{"n":"' + "x" * 100 + '"}', "arguments_too_large"),
    ],
)
async def test_invalid_arguments_never_reach_handler(arguments, code):
    async def forbidden(_args):
        pytest.fail("Invalid arguments reached handler")

    result, _ = await run_script(
        [call(arguments=arguments)], [tool(forbidden)], max_argument_bytes=80
    )
    assert result.tool_results[0].error["code"] == code
    assert result.tool_results[0].attempts == 0


@pytest.mark.asyncio
async def test_unknown_tool_yields_correctable_error():
    result, model = await run_script([call(name="invented")])
    assert result.tool_results[0].error["code"] == "unknown_tool"
    output = next(
        item for item in model.requests[1].items if item.get("type") == "function_call_output"
    )
    assert json.loads(output["output"])["ok"] is False


@pytest.mark.asyncio
async def test_invalid_protocol_names_and_model_text_fail_closed():
    result = await Agent(provider(ModelReply(calls=(call(name=chr(0xD800)),))), [tool()]).run(
        "test"
    )
    assert result.status == "protocol_error" and not result.tool_results
    result = await Agent(provider(ModelReply(text=chr(0xD800))), []).run("test")
    assert result.status == "provider_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_safe,expected", [(True, 2), (False, 1)])
async def test_retry_requires_explicit_tool_safety(retry_safe, expected):
    attempts = 0

    async def flaky(_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ToolTransientError("secret diagnostic")
        return "recovered"

    result, _ = await run_script(
        [call()], [tool(flaky, retry_safe=retry_safe, max_attempts=3)], retry_backoff_s=0
    )
    assert attempts == expected
    assert result.tool_results[0].ok is retry_safe
    assert "secret diagnostic" not in json.dumps(result.to_dict())


@pytest.mark.asyncio
async def test_tool_timeout_cancels_work_and_never_retries_ambiguous_effect():
    cleaned = asyncio.Event()
    attempts = 0

    async def hangs(_args):
        nonlocal attempts
        attempts += 1
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    result, _ = await run_script(
        [call()], [tool(hangs, timeout_s=0.01, retry_safe=True, max_attempts=3)]
    )
    assert result.tool_results[0].error["code"] == "tool_timeout"
    assert cleaned.is_set() and attempts == 1


@pytest.mark.asyncio
async def test_deadline_drains_all_inflight_tools():
    active = 0

    async def hangs(_args):
        nonlocal active
        active += 1
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    result, _ = await run_script(
        [call("a"), call("b")], [tool(hangs, parallel_safe=True)], run_timeout_s=0.02
    )
    assert result.status == "deadline" and active == 0


@pytest.mark.asyncio
async def test_external_cancellation_is_not_swallowed():
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def work(_args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    runtime = Agent(provider(ModelReply(calls=(call(),))), [tool(work)])
    task = asyncio.create_task(runtime.run("cancel"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("calls", [(call(), call()), (call("same"),)])
async def test_reused_call_ids_reject_entire_batch_before_effects(calls):
    ran = []

    async def track(args):
        ran.append(args)
        return 1

    script = (
        [ModelReply(calls=calls)]
        if len(calls) == 2
        else [ModelReply(calls=calls), ModelReply(calls=calls)]
    )
    result = await Agent(provider(*script), [tool(track)]).run("test")
    assert result.status == "protocol_error"
    assert len(ran) == (0 if len(calls) == 2 else 1)


@pytest.mark.asyncio
async def test_tool_budget_rejects_whole_batch():
    result, _ = await run_script([call("a"), call("b")], max_tool_calls=1)
    assert result.status == "tool_budget" and not result.tool_results


@pytest.mark.asyncio
async def test_semantic_repeat_breaker_ignores_ids_and_json_key_order():
    scenario = get_scenario("loop", delay_scale=0)
    result = await Agent(scenario.provider, scenario.tools).run(scenario.prompt)
    assert result.status == "repeated_tool_batch" and result.tool_calls == 2


@pytest.mark.asyncio
async def test_turn_budget():
    model = provider(
        *(ModelReply(calls=(call(str(i), arguments=json.dumps({"n": i})),)) for i in range(3))
    )
    result = await Agent(model, [tool()], limits=Limits(max_turns=2)).run("test")
    assert result.status == "turn_budget" and result.turns == 2


@pytest.mark.asyncio
async def test_observed_token_limit_stops_tools_and_caps_next_output_request():
    model = provider(ModelReply(calls=(call(),), usage=Usage(60, 50)))
    result = await Agent(model, [tool()], limits=Limits(max_total_tokens=100)).run("test")
    assert result.status == "token_budget" and result.tool_calls == 0
    assert model.requests[0].max_output_tokens == 100


@pytest.mark.asyncio
async def test_provider_retry_budget_and_fatal_error_policy():
    class Flaky:
        def __init__(self, error):
            self.attempts = 0
            self.error = error

        async def generate(self, _request):
            self.attempts += 1
            raise self.error("sensitive body")

    for error, expected in [(ProviderTransientError, 2), (ProviderError, 1)]:
        model = Flaky(error)
        result = await Agent(model, [], limits=Limits(retry_backoff_s=0)).run("test")
        assert result.status == "provider_error" and model.attempts == expected
        assert "sensitive body" not in json.dumps(result.to_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,code",
    [
        ("x" * 100, "output_too_large"),
        (float("nan"), "invalid_tool_output"),
        ({1, 2}, "invalid_tool_output"),
    ],
)
async def test_unserializable_or_oversized_output_is_structured_error(value, code):
    async def result(_args):
        return value

    run, _ = await run_script([call()], [tool(result)], max_tool_output_bytes=32)
    assert run.tool_results[0].error["code"] == code


@pytest.mark.asyncio
async def test_exception_content_does_not_leak_to_trace_or_result():
    async def failure(_args):
        raise RuntimeError("password=very-secret")

    telemetry = Telemetry()
    model = provider(ModelReply(calls=(call(),)), ModelReply(text="done"))
    result = await Agent(model, [tool(failure)], telemetry=telemetry).run("private prompt")
    data = json.dumps({"result": result.to_dict(), "spans": telemetry.events})
    assert "very-secret" not in data and "private prompt" not in data
    assert result.tool_results[0].error["code"] == "tool_error"


@pytest.mark.asyncio
async def test_raw_provider_output_items_are_replayed_but_not_exported():
    opaque = {"type": "reasoning", "encrypted_content": "opaque-secret"}
    model = provider(
        ModelReply(
            calls=(call(),),
            output_items=(
                opaque,
                {"type": "function_call", "call_id": "a", "name": "read", "arguments": '{"n":1}'},
            ),
        ),
        ModelReply(text="done"),
    )
    result = await Agent(model, [tool()]).run("test")
    assert opaque in model.requests[1].items
    assert "opaque-secret" not in json.dumps(result.to_dict())


def test_configuration_and_token_pricing_validation():
    with pytest.raises(ValueError):
        Limits(max_parallel=0)
    with pytest.raises(ValueError):
        Limits(run_timeout_s=float("inf"))
    with pytest.raises(ValueError):
        Usage(1, 0, 2)
    with pytest.raises(ValueError):
        Agent(
            provider(),
            [replace(tool(), parameters={"type": "object", "$ref": "https://example.com/schema"})],
        )
    with pytest.raises(ValueError):
        Agent(provider(), [tool(), tool()])
    assert Pricing(2, 8, 1, "test-only").cost(Usage(1000, 100, 200)) == pytest.approx(0.0026)
