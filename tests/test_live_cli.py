"""Exercise the explicit live command offline, including its privacy boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tool_loop import cli
from tool_loop.models import ModelReply, ModelRequest, ProviderError
from tool_loop.providers import OpenAIProvider as RealOpenAIProvider
from tool_loop.scenarios import get_scenario

_DUMMY_KEY = "synthetic-test-key-do-not-export"
_MODEL_ID = "explicit-test-model-id"


@pytest.fixture
def fake_live(monkeypatch: pytest.MonkeyPatch) -> type:
    class RecordingLiveProvider:
        instances: list[RecordingLiveProvider] = []
        failure: Exception | None = None
        usage_label = "mocked live usage; synthetic test tokens"

        def __init__(self, model: str) -> None:
            self.model = model
            self.closed = False
            self.fixture = get_scenario(delay_scale=0).provider
            self.instances.append(self)

        async def generate(self, request: ModelRequest) -> ModelReply:
            if self.failure is not None:
                raise self.failure
            return await self.fixture.generate(request)

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setenv("OPENAI_API_KEY", _DUMMY_KEY)
    monkeypatch.setattr(cli, "OpenAIProvider", RecordingLiveProvider)
    monkeypatch.setattr(cli, "get_scenario", lambda name: get_scenario(name, delay_scale=0))
    return RecordingLiveProvider


def command(output: Path, *extra: str) -> list[str]:
    return ["live", "--model", _MODEL_ID, "--output", str(output), *extra]


def artifacts(output: Path) -> tuple[dict[str, Any], str, str]:
    return (
        json.loads((output / "result.json").read_text()),
        (output / "report.html").read_text(),
        (output / "traces.jsonl").read_text(),
    )


def test_live_redacts_content_by_default_and_records_explicit_model(
    fake_live: type, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(command(tmp_path)) == 0
    document, report, traces = artifacts(tmp_path)
    assert document["status"] == "completed"
    assert document["model"] == _MODEL_ID
    assert document["model_source"] == "live OpenAI model"
    assert document["data_source"] == "synthetic in-memory incident evidence"
    assert document["usage_label"] == fake_live.usage_label
    assert (
        document["final_text"] == "Content omitted from artifacts; use --include-content to opt in."
    )
    assert all(item["value"] is None for item in document["tool_results"])
    assert all("details" not in item["error"] for item in document["tool_results"] if item["error"])
    assert "database pool from 30" not in json.dumps(document)
    assert "database pool from 30" not in report
    assert "pool_limit" not in report
    assert "pool_limit" not in traces
    assert "database pool from 30" not in traces
    assert document["cost_usd"] is None
    assert document["pricing_label"] is None
    assert "Unpriced" in report
    assert fake_live.instances[0].closed is True
    captured = capsys.readouterr()
    assert "Likely cause" in captured.out  # The operator still receives the answer.
    assert _DUMMY_KEY not in captured.out + captured.err + json.dumps(document) + report + traces


def test_live_content_export_requires_explicit_opt_in_and_traces_still_omit_it(
    fake_live: type, tmp_path: Path
) -> None:
    assert cli.main(command(tmp_path, "--include-content")) == 0
    document, report, traces = artifacts(tmp_path)
    assert "SYNTHETIC INCIDENT DRILL" in document["final_text"]
    assert "database pool from 30" in document["final_text"]
    assert "database pool from 30" in report
    logs = next(
        item for item in document["tool_results"] if item["name"] == "query_logs" and item["ok"]
    )
    assert logs["value"]["entries"][0]["pool_limit"] == 3
    assert "pool_limit" in report
    assert "pool_limit" not in traces
    assert "database pool from 30" not in traces
    assert _DUMMY_KEY not in json.dumps(document) + report + traces
    assert fake_live.instances[0].closed is True


_PRICING = [
    ("--input-rate", "2"),
    ("--output-rate", "8"),
    ("--cached-rate", "0.5"),
    ("--price-label", "synthetic test rate assumption"),
]


@pytest.mark.parametrize("missing", range(4))
def test_live_pricing_requires_each_of_the_four_fields(
    fake_live: type, tmp_path: Path, capsys: pytest.CaptureFixture[str], missing: int
) -> None:
    partial = [value for index, pair in enumerate(_PRICING) if index != missing for value in pair]
    assert cli.main(command(tmp_path, *partial)) == 2
    assert "all three rates and --price-label" in capsys.readouterr().err
    assert fake_live.instances == []
    assert not (tmp_path / "result.json").exists()


def test_live_explicit_pricing_counts_cached_tokens_at_their_own_rate(
    fake_live: type, tmp_path: Path
) -> None:
    options = [value for pair in _PRICING for value in pair]
    assert cli.main(command(tmp_path, *options)) == 0
    document, report, traces = artifacts(tmp_path)
    assert document["usage"] == {
        "input_tokens": 2780,
        "output_tokens": 650,
        "cached_input_tokens": 768,
    }
    expected = ((2780 - 768) * 2 + 768 * 0.5 + 650 * 8) / 1_000_000
    assert document["cost_usd"] == pytest.approx(expected)
    assert document["pricing_label"] == "synthetic test rate assumption"
    assert "$0.009608" in report
    assert "$9.608" in report
    assert "agent.cost_usd" in traces
    assert fake_live.instances[0].closed is True


def test_live_auth_failure_is_a_graceful_run_failure_and_closes_provider(
    fake_live: type, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_live.failure = ProviderError("OpenAI request rejected (HTTP 401).")
    assert cli.main(command(tmp_path)) == 1
    document, report, traces = artifacts(tmp_path)
    assert document["status"] == "provider_error"
    assert "Run stopped: provider_error" in capsys.readouterr().out
    assert fake_live.instances[0].closed is True
    assert _DUMMY_KEY not in json.dumps(document) + report + traces
    assert "Traceback" not in traces


def test_live_output_failure_still_closes_provider(
    fake_live: type, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_report(*_args: Any) -> None:
        raise OSError("Synthetic report output failure")

    monkeypatch.setattr(cli, "write_report", fail_report)
    assert cli.main(command(tmp_path)) == 2
    assert fake_live.instances[0].closed is True


def test_live_malformed_base_url_is_sanitized_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = "sensitive-port-marker"
    monkeypatch.setenv("OPENAI_API_KEY", _DUMMY_KEY)
    monkeypatch.setenv("OPENAI_BASE_URL", f"https://example.invalid:{marker}")
    monkeypatch.setattr(cli, "OpenAIProvider", RealOpenAIProvider)
    assert cli.main(command(tmp_path)) == 2
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err
    assert _DUMMY_KEY not in captured.out + captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "result.json").exists()
