import re
import stat
from html.parser import HTMLParser

from tool_loop.report import write_report


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_untrusted_content_cannot_create_markup_or_script(tmp_path):
    malicious = '</pre><script>alert("stolen")</script><img src=x onerror=alert(1)>'
    path = write_report(
        {
            "run_id": malicious,
            "status": malicious,
            "final_text": malicious,
            "events": [{"type": malicious, "at_ms": 1, "details": malicious}],
            "tool_results": [{"name": malicious, "ok": False, "error": malicious}],
        },
        [{"name": malicious, "span_id": malicious, "attributes": {"message": malicious}}],
        tmp_path,
    )
    document = path.read_text()
    parser = _Tags()
    parser.feed(document)
    assert not any(tag in {"script", "img", "iframe"} for tag, _ in parser.tags)
    assert not any(key.startswith("on") for _, attrs in parser.tags for key in attrs)
    assert "&lt;script&gt;" in document
    assert any(
        tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"
        for tag, attrs in parser.tags
    )


def test_report_shows_measured_metrics_and_parallel_waterfall(tmp_path):
    spans = [
        {"name": "llm.call", "start_time_ms": 0, "end_time_ms": 10, "duration_ms": 10},
        {"name": "llm.call", "start_time_ms": 110, "end_time_ms": 140, "duration_ms": 30},
        {
            "name": "tool.execute",
            "start_time_ms": 10,
            "end_time_ms": 110,
            "duration_ms": 100,
            "attributes": {"tool.name": "alpha"},
        },
        {
            "name": "tool.execute",
            "start_time_ms": 10,
            "end_time_ms": 60,
            "duration_ms": 50,
            "attributes": {"tool.name": "beta"},
        },
    ]
    document = write_report(
        {
            "run_id": "run-a",
            "status": "completed",
            "duration_ms": 140,
            "cost_usd": 0.00125,
            "turns": 2,
            "tool_calls": 2,
            "usage": {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 25},
            "final_text": "Verified answer.",
        },
        spans,
        tmp_path,
    ).read_text()
    assert "$0.001250" in document
    assert "$1.250" in document
    assert "120" in document
    assert "25 cached" in document
    assert "20.0 ms" in document  # interpolated model p50
    assert "29.0 ms" in document  # interpolated model p95
    assert "97.5 ms" in document  # interpolated tool p95
    bars = re.findall(r'class="bar tool" style="left:([^;]+);width:([^\"]+)"', document)
    assert len(bars) == 2
    assert bars[0][0] == bars[1][0]  # simultaneous starts share a horizontal position
    assert float(bars[0][1].rstrip("%")) > float(bars[1][1].rstrip("%"))


def test_missing_usage_and_price_do_not_invent_measurements(tmp_path):
    path = write_report({"status": "turn_limit", "final_text": None}, [], tmp_path)
    document = path.read_text()
    assert path.name == "report.html"
    assert "Unpriced" in document
    assert "$0.000000" not in document
    assert "No final response was produced." in document
    assert "End reason: turn_limit" in document
    assert "No model or tool spans were recorded." in document


def test_source_labels_make_fixture_usage_and_fictional_pricing_explicit(tmp_path):
    document = write_report(
        {
            "status": "completed",
            "final_text": "Done",
            "cost_usd": 0.001,
            "model_source": "scripted protocol fixture; no inference",
            "usage_label": "synthetic fixture tokens; not measured model usage",
            "data_source": "synthetic in-memory incident evidence",
            "pricing_label": "fictional demonstration rates <not a price quote>",
        },
        [],
        tmp_path,
    ).read_text()
    assert "scripted protocol fixture; no inference" in document
    assert "synthetic fixture tokens; not measured model usage" in document
    assert "synthetic in-memory incident evidence" in document
    assert "fictional demonstration rates &lt;not a price quote&gt;" in document
    assert "reported usage × explicit rate assumption" in document
    assert "provider usage × configured rates" not in document
    assert document.index("scripted protocol fixture") < document.index('<div class="metrics">')


def test_cancelled_tool_is_distinguished_from_completed_failure(tmp_path):
    document = write_report(
        {
            "status": "deadline",
            "tool_results": [{"name": "slow", "ok": False, "error": {"code": "cancelled"}}],
        },
        [],
        tmp_path,
    ).read_text()
    assert "CANCELLED" in document


def test_reused_content_report_is_owner_only(tmp_path):
    path = tmp_path / "report.html"
    path.write_text("previous artifact")
    path.chmod(0o644)
    write_report({"final_text": "private output"}, [], tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
