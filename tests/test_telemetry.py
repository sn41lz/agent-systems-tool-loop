import asyncio
import json
import stat

import pytest
from opentelemetry import trace

from tool_loop.telemetry import Telemetry


def test_parallel_spans_inherit_parent_and_are_exported(tmp_path):
    telemetry = Telemetry(tmp_path)

    async def worker(name):
        with telemetry.span("tool.execute", {"tool.name": name}):
            await asyncio.sleep(0)

    async def run():
        with telemetry.span("agent.run"):
            await asyncio.gather(worker("a"), worker("b"))

    asyncio.run(run())
    telemetry.close()

    records = [json.loads(line) for line in (tmp_path / "traces.jsonl").read_text().splitlines()]
    assert records == telemetry.events
    parent = next(span for span in records if span["name"] == "agent.run")
    children = [span for span in records if span["name"] == "tool.execute"]
    assert len(children) == 2
    assert {span["parent_span_id"] for span in children} == {parent["span_id"]}
    assert {span["trace_id"] for span in records} == {parent["trace_id"]}
    assert len({span["span_id"] for span in records}) == 3
    assert all(span["status"] == "OK" for span in records)
    assert all(span["start_time_ms"] <= span["end_time_ms"] for span in records)
    assert all(span["duration_ms"] >= 0 for span in records)


def test_instances_do_not_install_or_mix_global_provider():
    before = trace.get_tracer_provider()
    first, second = Telemetry(), Telemetry()
    with first.span("first"):
        pass
    with second.span("second"):
        pass
    first.close()
    second.close()
    assert trace.get_tracer_provider() is before
    assert [span["name"] for span in first.events] == ["first"]
    assert [span["name"] for span in second.events] == ["second"]
    assert first.events[0]["trace_id"] != second.events[0]["trace_id"]


def test_exception_marks_error_without_exporting_message(tmp_path):
    with Telemetry(tmp_path) as telemetry:
        with pytest.raises(ValueError):
            with telemetry.span("tool.execute", {"tool.name": "calculator"}):
                raise ValueError("sensitive-prompt-contents")
    assert telemetry.events[0]["status"] == "ERROR"
    assert "sensitive-prompt-contents" not in (tmp_path / "traces.jsonl").read_text()
    telemetry.close()
    with pytest.raises(RuntimeError, match="closed"):
        with telemetry.span("too-late"):
            pass


def test_explicit_error_status_is_preserved():
    with Telemetry() as telemetry:
        with telemetry.span("tool.execute") as span:
            span.set_attribute("tool.attempt", 2)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
    assert telemetry.events[0]["status"] == "ERROR"
    assert telemetry.events[0]["attributes"]["tool.attempt"] == 2


def test_cancelled_async_span_is_error_and_does_not_swallow_cancellation():
    with Telemetry() as telemetry:

        async def run():
            with telemetry.span("tool.execute"):
                raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    assert telemetry.events[0]["status"] == "ERROR"
    assert telemetry.events[0]["attributes"]["error.type"] == "cancelled"


def test_reused_trace_artifact_is_owner_only(tmp_path):
    path = tmp_path / "traces.jsonl"
    path.write_text("previous artifact")
    path.chmod(0o644)
    with Telemetry(tmp_path) as telemetry:
        with telemetry.span("agent.run"):
            pass
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
