"""Verify the reproducible review path, not just runtime internals."""

import json

import pytest

from tool_loop.benchmark import benchmark, percentile
from tool_loop.cli import main


def test_demo_creates_consistent_trace_result_and_report(tmp_path):
    assert main(["demo", "--output", str(tmp_path)]) == 0
    result = json.loads((tmp_path / "result.json").read_text())
    spans = [json.loads(line) for line in (tmp_path / "traces.jsonl").read_text().splitlines()]
    assert result["status"] == "completed"
    assert result["model_source"] == "scripted protocol fixture; no inference"
    assert len([span for span in spans if span["name"] == "llm.call"]) == 3
    assert len([span for span in spans if span["name"] == "tool.execute"]) == 5
    assert len({span["trace_id"] for span in spans}) == 1
    assert (tmp_path / "report.html").exists()


def test_loop_demo_has_expected_stop_reason(tmp_path):
    assert main(["demo", "--scenario", "loop", "--output", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "repeated_tool_batch"


def test_live_requires_explicit_key_without_network(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["live", "--model", "test-model", "--output", str(tmp_path)]) == 2
    assert not (tmp_path / "result.json").exists()


def test_invalid_parallelism_fails_with_configuration_message(tmp_path, capsys):
    assert main(["demo", "--parallel", "0", "--output", str(tmp_path)]) == 2
    assert "max_parallel" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_benchmark_pairs_configurations_and_retains_all_raw_samples(tmp_path):
    summary = await benchmark(3, output=tmp_path, delay_scale=0, warmups=1)
    rows = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert len(rows) == 6
    for pair in range(3):
        assert {row["configuration"] for row in rows if row["pair"] == pair} == {
            "sequential",
            "parallel",
        }
    assert summary["all_protocol_checks_passed"]
    assert summary["actual_model_api_calls"] == 0
    assert summary["actual_model_api_cost_usd"] == 0
    assert (
        summary["groups"]["parallel"]["synthetic_tokens_per_run"]
        == summary["groups"]["sequential"]["synthetic_tokens_per_run"]
    )
    assert summary["method"]["discarded_warmup_pairs"] == 1
    assert "Not measured" in summary["live_model_quality"]
    assert (tmp_path / "latency.svg").exists()


def test_percentile_interpolates_without_claiming_unobserved_tail():
    assert percentile([100, 200, 300], 0.5) == 200
    assert percentile([100, 200, 300], 0.95) == 290
    with pytest.raises(ValueError):
        percentile([], 0.95)
