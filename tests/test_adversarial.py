"""Adversarial inputs and cancellation must preserve the runtime's contracts."""

import asyncio
import copy
import json

import pytest

from tool_loop.models import Limits, ModelReply, Tool, ToolCall, ToolTransientError
from tool_loop.providers import FixtureProvider
from tool_loop.runtime import Agent

OBJECT = {"type": "object"}


def scripted(*calls: ToolCall) -> FixtureProvider:
    return FixtureProvider([ModelReply(calls=calls), ModelReply(text="recovered")])


@pytest.mark.parametrize("arguments", ['{"value":"\ud800"}', r'{"value":"\ud800"}'])
async def test_unpaired_unicode_surrogates_never_reach_a_tool(arguments):
    invoked = False

    async def forbidden(_args):
        nonlocal invoked
        invoked = True
        return "unreachable"

    model = scripted(ToolCall("unicode", "inspect", arguments))
    result = await Agent(model, [Tool("inspect", "Inspect an object", OBJECT, forbidden)]).run(
        "Inspect only valid JSON objects."
    )

    assert not invoked
    assert result.status == "completed"
    assert len(result.tool_results) == 1
    assert result.tool_results[0].error["code"] == "invalid_json"
    assert result.tool_results[0].attempts == 0


async def test_small_but_deeply_nested_arguments_are_rejected_before_validation():
    invoked = False

    async def forbidden(_args):
        nonlocal invoked
        invoked = True
        return "unreachable"

    nested = {}
    for _ in range(48):
        nested = {"child": nested}
    # A valid recursive schema cannot make model-controlled depth unbounded.
    recursive = {"type": "object", "properties": {"child": {"$ref": "#"}}}
    arguments = json.dumps(nested)
    assert len(arguments.encode()) < 1024
    result = await Agent(
        scripted(ToolCall("deep", "inspect", arguments)),
        [Tool("inspect", "Inspect a tree", recursive, forbidden)],
    ).run("Inspect a bounded tree.")

    assert not invoked
    assert result.status == "completed"
    assert result.tool_results[0].error["code"] == "invalid_json"
    assert result.tool_results[0].attempts == 0


@pytest.mark.parametrize("reference", ["#/$defs/missing", "#missing-anchor"])
def test_unresolved_local_schema_references_fail_at_registration(reference):
    async def forbidden(_args):
        pytest.fail("Invalid schemas must never be registered or executed")

    schema = {"type": "object", "properties": {"value": {"$ref": reference}}}
    with pytest.raises(ValueError):
        Agent(FixtureProvider([]), [Tool("inspect", "Inspect an object", schema, forbidden)])


async def test_transient_retry_receives_original_arguments_after_nested_mutation():
    received = []
    original = {"nested": {"items": ["original"]}}

    async def mutating_tool(arguments):
        received.append(copy.deepcopy(arguments))
        if len(received) == 1:
            arguments["nested"]["items"].append("mutated during failed attempt")
            raise ToolTransientError("retry safe read failed")
        return arguments["nested"]["items"]

    result = await Agent(
        scripted(ToolCall("retry", "inspect", json.dumps(original))),
        [
            Tool(
                "inspect",
                "Read an object with retry-safe semantics",
                OBJECT,
                mutating_tool,
                retry_safe=True,
                max_attempts=2,
            )
        ],
        limits=Limits(retry_backoff_s=0),
    ).run("Retry the read within its budget.")

    assert received == [original, original]
    assert result.status == "completed"
    assert result.tool_results[0].ok
    assert result.tool_results[0].attempts == 2
    assert result.tool_results[0].value == ["original"]


async def test_deadline_keeps_completed_cancelled_and_unstarted_results_in_order():
    slow_started = asyncio.Event()
    slow_cleaned = asyncio.Event()
    effect_invoked = False

    async def slow(_args):
        slow_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            slow_cleaned.set()

    async def fast(_args):
        await slow_started.wait()
        return "read completed before deadline"

    async def effect(_args):
        nonlocal effect_invoked
        effect_invoked = True
        return "unreachable"

    model = scripted(
        ToolCall("slow-call", "slow", "{}"),
        ToolCall("fast-call", "fast", "{}"),
        ToolCall("effect-call", "effect", "{}"),
    )
    result = await Agent(
        model,
        [
            Tool("slow", "A cooperative read", OBJECT, slow, parallel_safe=True, timeout_s=1),
            Tool("fast", "A quick read", OBJECT, fast, parallel_safe=True),
            Tool("effect", "A serial side-effect barrier", OBJECT, effect),
        ],
        limits=Limits(max_parallel=2, run_timeout_s=0.05),
    ).run("Read before considering a side effect.")

    assert result.status == "deadline"
    assert slow_started.is_set() and slow_cleaned.is_set()
    assert not effect_invoked
    assert result.tool_calls == 3
    assert [item.call_id for item in result.tool_results] == [
        "slow-call",
        "fast-call",
        "effect-call",
    ]
    slow_result, fast_result, effect_result = result.tool_results
    assert not slow_result.ok and slow_result.error["code"] == "cancelled"
    assert fast_result.ok and fast_result.value == "read completed before deadline"
    assert not effect_result.ok and effect_result.error["code"] == "not_executed"
    assert effect_result.attempts == 0
    assert len(model.requests) == 1
