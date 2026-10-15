"""Exercise the public artifact paths without credentials or paid API calls."""

import json

import pytest

from tool_loop import cli
from tool_loop.benchmark import benchmark


def test_demo_artifacts_agree_and_disclose_synthetic_sources(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert cli.main(["demo", "--output", str(tmp_path)]) == 0
    result = json.loads((tmp_path / "result.json").read_text())
    spans = [json.loads(line) for line in (tmp_path / "traces.jsonl").read_text().splitlines()]
    report = (tmp_path / "report.html").read_text()
    assert result["status"] == "completed"
    assert result["model_source"] == "scripted protocol fixture; no inference"
    assert "synthetic" in result["usage_label"]
    assert "not provider pricing" in result["pricing_label"]
    assert all(result[key] in report for key in ("model_source", "usage_label", "data_source"))
    run = next(span for span in spans if span["name"] == "agent.run")
    assert run["attributes"]["agent.run_id"] == result["run_id"]
    assert run["attributes"]["gen_ai.usage.input_tokens"] == result["usage"]["input_tokens"]
    assert run["attributes"]["gen_ai.usage.output_tokens"] == result["usage"]["output_tokens"]
    assert run["attributes"]["agent.cost_usd"] == result["cost_usd"]
    assert len([span for span in spans if span["name"] == "tool.execute"]) == result["tool_calls"]


def test_live_without_key_exits_before_client_or_artifact_creation(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden_client(*args, **kwargs):
        pytest.fail("Missing credentials must stop before a client is constructed")

    monkeypatch.setattr(cli, "OpenAIProvider", forbidden_client)
    output = tmp_path / "live"
    assert cli.main(["live", "--model", "uncontacted-model", "--output", str(output)]) == 2
    assert "Set OPENAI_API_KEY" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.asyncio
async def test_benchmark_artifacts_are_paired_and_explicit_about_inference(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    summary = await benchmark(runs=3, output=tmp_path, warmups=0, delay_scale=0)
    rows = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert len(rows) == 6
    assert summary["actual_model_api_calls"] == 0
    assert summary["actual_model_api_cost_usd"] == 0
    assert summary["all_protocol_checks_passed"] is True
    assert "scripted" in summary["live_model_quality"]
    assert "Not measured" in summary["streaming_ttft"]
    assert "not provider pricing" in summary["pricing_assumption"]["label"]
    for pair in range(3):
        paired = [row for row in rows if row["pair"] == pair]
        assert {row["max_parallel"] for row in paired} == {1, 4}
        assert paired[0]["usage"] == paired[1]["usage"]
        assert paired[0]["illustrative_cost_usd"] == paired[1]["illustrative_cost_usd"]
    assert json.loads((tmp_path / "summary.json").read_text()) == summary
    assert "No live model calls" in (tmp_path / "summary.md").read_text()
