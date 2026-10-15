"""Generate an offline, dependency-free run report from result and span records.

No scripts, network requests, or external fonts are required. All model and tool
content is HTML-escaped, including content placed in disclosure panels. A report
is an explicit local artifact: unlike telemetry, it includes the final response
and tool result details and should be shared with the same care as those results.
"""

from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path
from typing import Any


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _duration(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1_000:
        return f"{value / 1_000:,.2f} s"
    return f"{value:,.1f} ms"


def _pretty(value: Any) -> str:
    return _escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _metric(label: str, value: Any, detail: str = "") -> str:
    return (
        '<article class="metric"><div class="eyebrow">'
        + _escape(label)
        + '</div><div class="metric-value">'
        + _escape(value)
        + '</div><div class="muted metric-detail">'
        + _escape(detail)
        + "</div></article>"
    )


def _span_label(span: dict[str, Any]) -> str:
    attributes = span.get("attributes") or {}
    tool_name = attributes.get("tool.name", attributes.get("tool_name"))
    return str(tool_name or span.get("name", "span"))


def _waterfall(spans: list[dict[str, Any]]) -> str:
    operations = [span for span in spans if span.get("name") in {"llm.call", "tool.execute"}]
    if not operations:
        return '<p class="empty">No model or tool spans were recorded.</p>'
    operations.sort(key=lambda span: _number(span.get("start_time_ms")))
    origin = min(_number(span.get("start_time_ms")) for span in operations)
    end = max(
        _number(span.get("end_time_ms"), _number(span.get("start_time_ms"))) for span in operations
    )
    total = max(end - origin, 0.001)
    rows = []
    for span in operations:
        offset = max(0, _number(span.get("start_time_ms")) - origin)
        duration = max(0, _number(span.get("duration_ms")))
        left = min(99.8, offset / total * 100)
        width = min(100 - left, max(0.2, duration / total * 100))
        kind = "model" if span.get("name") == "llm.call" else "tool"
        failed = span.get("status") == "ERROR"
        label = _span_label(span)
        title = f"{label} · +{_duration(offset)} · {_duration(duration)}"
        rows.append(
            '<div class="waterfall-row"><div class="operation-name" title="'
            + _escape(label)
            + '"><span class="kind '
            + kind
            + '">'
            + ("LLM" if kind == "model" else "TOOL")
            + "</span> "
            + _escape(label)
            + '</div><div class="track"><div class="bar '
            + ("failed" if failed else kind)
            + f'" style="left:{left:.5f}%;width:{width:.5f}%" title="'
            + _escape(title)
            + '"></div></div><div class="operation-duration">'
            + _duration(duration)
            + "</div></div>"
        )
    return (
        '<div class="waterfall-axis"><span>Elapsed from first operation</span>'
        + "<span>0 ms → "
        + _duration(total)
        + '</span></div><div class="waterfall">'
        + "".join(rows)
        + '</div><p class="caption">Bars share one clock. Horizontal overlap shows '
        + "concurrent work; each row is a recorded operation.</p>"
    )


def _tool_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return '<p class="empty">No tool results.</p>'
    rows = []
    for result in results:
        ok = bool(result.get("ok"))
        error = result.get("error") or {}
        cancelled = isinstance(error, dict) and error.get("code") in {
            "cancelled",
            "tool_cancelled",
            "execution_cancelled",
        }
        outcome = "OK" if ok else ("CANCELLED" if cancelled else "ERROR")
        cached = " · cached" if result.get("cached") else ""
        attempts = result.get("attempts", 1)
        payload = result.get("value") if ok else result.get("error")
        rows.append(
            '<details class="tool-result"><summary><span class="outcome '
            + ("success" if ok else "error")
            + '">'
            + outcome
            + "</span><strong>"
            + _escape(result.get("name", "tool"))
            + '</strong><span class="muted tool-result-meta">'
            + _escape(attempts)
            + " attempt(s)"
            + cached
            + " · "
            + _duration(_number(result.get("duration_ms")))
            + '</span></summary><div class="result-body"><div class="caption">Call '
            + _escape(result.get("call_id", "—"))
            + "</div><pre>"
            + _pretty(payload)
            + "</pre></div></details>"
        )
    return "".join(rows)


def _events(events: list[dict[str, Any]]) -> str:
    if not events:
        return '<p class="empty">No runtime events.</p>'
    rows = []
    for event in events:
        detail = {key: value for key, value in event.items() if key not in {"type", "at_ms"}}
        rows.append(
            '<details class="event"><summary><span class="event-time">+'
            + _duration(_number(event.get("at_ms")))
            + "</span><span>"
            + _escape(event.get("type", "event"))
            + "</span></summary><pre>"
            + _pretty(detail)
            + "</pre></details>"
        )
    return "".join(rows)


def _trace_table(spans: list[dict[str, Any]]) -> str:
    if not spans:
        return '<p class="empty">No spans were recorded.</p>'
    by_id = {span.get("span_id"): span for span in spans}
    rows = []
    for span in sorted(spans, key=lambda item: _number(item.get("start_time_ms"))):
        depth = 0
        parent_id = span.get("parent_span_id")
        seen = {span.get("span_id")}
        while parent_id in by_id and parent_id not in seen:
            seen.add(parent_id)
            depth += 1
            parent_id = by_id[parent_id].get("parent_span_id")
        rows.append(
            '<tr><td style="padding-left:'
            + str(16 + min(depth, 8) * 16)
            + 'px"><details><summary>'
            + _escape(span.get("name", "span"))
            + "</summary><pre>"
            + _pretty(span.get("attributes", {}))
            + '</pre></details></td><td class="mono">'
            + _escape(span.get("span_id", "—"))
            + "</td><td>"
            + _escape(span.get("status", "UNSET"))
            + '</td><td class="numeric">'
            + _duration(_number(span.get("duration_ms")))
            + "</td></tr>"
        )
    return (
        '<div class="table-scroll"><table><thead><tr><th>Span / attributes</th>'
        + '<th>Span ID</th><th>Status</th><th class="numeric">Duration</th></tr></thead>'
        + "<tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


_CSS = """
:root{color-scheme:dark;--bg:#0a1018;--panel:#111b27;--line:#243344;--text:#edf4f8;--muted:#94a8ba;--cyan:#67e8d2;--orange:#ffbb77;--red:#ff8b99}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.5}
main{max-width:1240px;margin:auto;padding:48px 32px 40px}.topline{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:34px}.brand{font-size:12px;letter-spacing:.2em;font-weight:800;color:var(--cyan)}.topnote{font-size:12px;color:var(--muted)}
.hero{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin-bottom:28px}h1{font-size:clamp(30px,5vw,52px);letter-spacing:-.045em;line-height:1.1;margin:0 0 13px}h2{font-size:20px;letter-spacing:-.025em;margin:0 0 5px}.subtitle{color:var(--muted);margin:0;max-width:760px}.status{font-size:12px;font-weight:800;letter-spacing:.09em;border:1px solid var(--line);border-radius:100px;padding:10px 16px;white-space:nowrap}.status.success{border-color:#38756f;color:var(--cyan)}.status.error{border-color:#7d414b;color:var(--red)}
.run-id,.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}.run-id{color:var(--muted);overflow-wrap:anywhere;margin:0 0 24px}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:24px 0}.metric{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px;min-height:118px}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:10px;color:var(--muted);font-weight:750}.metric-value{font-size:28px;letter-spacing:-.04em;font-weight:700;margin:8px 0 2px}.muted{color:var(--muted)}.metric-detail{font-size:11px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:26px;margin:18px 0}.section-head{margin-bottom:24px}.caption{font-size:12px;color:var(--muted);margin-bottom:0}.final-response{white-space:pre-wrap;overflow-wrap:anywhere;font-size:15px;border-left:2px solid var(--cyan);padding-left:20px;margin:22px 0 5px}.empty{color:var(--muted);font-size:13px;padding:12px 0}
.provenance{border:1px solid var(--line);border-left:3px solid var(--orange);border-radius:9px;padding:17px 20px;display:grid;grid-template-columns:1fr 1fr;gap:14px 24px;background:#141c25}.provenance dl{margin:0;min-width:0}.provenance dt{color:var(--orange);font-size:9px;text-transform:uppercase;letter-spacing:.12em;font-weight:750}.provenance dd{font-size:12px;margin:4px 0 0;overflow-wrap:anywhere}
.waterfall-axis{display:flex;justify-content:space-between;gap:15px;color:var(--muted);font-size:11px;padding-bottom:12px}.waterfall-row{display:grid;grid-template-columns:220px minmax(80px,1fr) 90px;gap:16px;align-items:center;min-height:41px}.operation-name{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.kind{font-size:9px;letter-spacing:.08em;font-weight:800;display:inline-block;width:33px}.kind.model{color:var(--orange)}.kind.tool{color:var(--cyan)}.track{height:24px;position:relative;border-left:1px solid var(--line);background:repeating-linear-gradient(to right,transparent 0,transparent calc(25% - 1px),#24334488 calc(25% - 1px),#24334488 25%)}.bar{position:absolute;top:5px;height:14px;border-radius:3px;min-width:2px}.bar.model{background:var(--orange)}.bar.tool{background:var(--cyan)}.bar.failed{background:var(--red)}.operation-duration{text-align:right;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:18px}.two-col .panel{margin:0;min-width:0}.outcome{font-size:9px;letter-spacing:.08em;font-weight:800;display:inline-block;min-width:46px}.outcome.success{color:var(--cyan)}.outcome.error{color:var(--red)}details summary{cursor:pointer;list-style:none}details summary::-webkit-details-marker{display:none}details summary:before{content:"+";color:var(--muted);margin-right:10px}details[open]>summary:before{content:"−"}.tool-result,.event{border-top:1px solid var(--line);padding:13px 0}.tool-result summary{font-size:12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}.tool-result summary strong{font-size:13px}.tool-result-meta{font-size:11px;margin-left:auto}.result-body{padding:12px 0 0 23px}pre{white-space:pre-wrap;overflow-wrap:anywhere;color:#b7cddd;background:#0a121c;border:1px solid #203040;border-radius:7px;padding:14px;font-size:11px;line-height:1.6;max-height:420px;overflow:auto}.event summary{font-size:12px}.event-time{font-variant-numeric:tabular-nums;color:var(--muted);display:inline-block;min-width:95px;font-size:11px}
.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px}th{text-align:left;font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:600}th,td{padding:12px 16px;border-bottom:1px solid var(--line);vertical-align:top}.numeric{text-align:right;white-space:nowrap}td pre{max-width:650px}.trace-note{overflow-wrap:anywhere}.footer{display:flex;justify-content:space-between;gap:20px;color:var(--muted);font-size:11px;margin-top:28px;border-top:1px solid var(--line);padding-top:20px}
@media(max-width:850px){main{padding:28px 18px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.two-col{grid-template-columns:1fr}.hero{flex-direction:column}.waterfall-row{grid-template-columns:140px minmax(60px,1fr) 65px;gap:8px}.panel{padding:20px}.operation-name,.operation-duration{font-size:10px}.topnote{display:none}.provenance{grid-template-columns:1fr}}
@media(max-width:500px){.waterfall-row{grid-template-columns:94px minmax(0,1fr) 56px;gap:6px}.panel,.metric,.hero>div{min-width:0}.metric{padding:16px 14px}.metric-value{font-size:24px;overflow-wrap:anywhere}.metric-detail,.eyebrow,.caption,.subtitle,.event summary,.tool-result summary strong{overflow-wrap:anywhere}.table-scroll{min-width:0;max-width:100%}.status{max-width:100%;white-space:normal;overflow-wrap:anywhere}.tool-result summary strong{min-width:0}.operation-duration{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.footer{flex-direction:column;gap:8px}.brand{font-size:10px;letter-spacing:.15em}}
@media print{:root{color-scheme:light;--bg:#fff;--panel:#fff;--line:#cbd5df;--text:#172c3a;--muted:#536777;--cyan:#147f76;--orange:#a26216;--red:#b43244}main{padding:15px;max-width:none}.panel,.metric{break-inside:avoid}.two-col{display:block}.two-col .panel{margin:18px 0}.metric-value{font-size:23px}pre{background:#f5f7fa;color:#243849}details[open] pre{max-height:none}.footer{font-size:9px}}
"""


def write_report(result: dict[str, Any], spans: list[dict[str, Any]], output_dir: Path) -> Path:
    """Write ``report.html`` and return its path. Costs remain absent if unknown.

    Latency percentiles describe this run's observed span durations, using linear
    interpolation. Cost per 1,000 runs is a projection of this exact run, not a
    benchmark or an assertion about future traffic.
    """
    usage = result.get("usage") or {}
    input_tokens = int(_number(usage.get("input_tokens")))
    output_tokens = int(_number(usage.get("output_tokens")))
    cached_tokens = int(_number(usage.get("cached_input_tokens")))
    duration_ms = _number(result.get("duration_ms"))
    tools = result.get("tool_results") or []
    llm_durations = [
        _number(span.get("duration_ms")) for span in spans if span.get("name") == "llm.call"
    ]
    tool_durations = [
        _number(span.get("duration_ms")) for span in spans if span.get("name") == "tool.execute"
    ]
    known_cost = result.get("cost_usd") is not None
    cost = _number(result.get("cost_usd"))
    status = str(result.get("status", "unknown"))
    success = status in {"completed", "complete", "success", "succeeded", "finished"}
    failure_count = sum(not bool(tool.get("ok")) for tool in tools)
    tool_count = result.get("tool_calls", len(tools))
    turns = result.get("turns", 0)
    metrics = "".join(
        [
            _metric("Wall time", _duration(duration_ms), f"{turns} model turn(s)"),
            _metric(
                "Reported tokens",
                f"{input_tokens + output_tokens:,}",
                f"{input_tokens:,} input · {output_tokens:,} output · {cached_tokens:,} cached",
            ),
            _metric(
                "Run cost",
                f"${cost:.6f}" if known_cost else "Unpriced",
                "Explicit pricing configuration required"
                if not known_cost
                else "USD · reported usage × explicit rate assumption",
            ),
            _metric(
                "Cost / 1,000 runs",
                f"${cost * 1_000:.3f}" if known_cost else "—",
                "Projection from this run's workload",
            ),
            _metric(
                "LLM latency · p50",
                _duration(_percentile(llm_durations, 0.5)),
                f"{len(llm_durations)} observed call(s)",
            ),
            _metric(
                "LLM latency · p95",
                _duration(_percentile(llm_durations, 0.95)),
                "Within-run span duration distribution",
            ),
            _metric(
                "Tool latency · p95",
                _duration(_percentile(tool_durations, 0.95)),
                f"p50 {_duration(_percentile(tool_durations, 0.5))}",
            ),
            _metric(
                "Tool calls",
                tool_count,
                f"{failure_count} failed result(s) · {sum(bool(tool.get('cached')) for tool in tools)} cached",
            ),
        ]
    )
    trace_ids = sorted({str(span.get("trace_id")) for span in spans if span.get("trace_id")})
    provenance = (
        '<aside class="provenance" aria-label="Evidence sources">'
        + "".join(
            "<dl><dt>" + _escape(label) + "</dt><dd>" + _escape(value) + "</dd></dl>"
            for label, value in (
                ("Model source", result.get("model_source") or "Not supplied"),
                ("Token accounting", result.get("usage_label") or "Not supplied"),
                ("Tool evidence", result.get("data_source") or "Not supplied"),
                (
                    "Pricing assumption",
                    result.get("pricing_label") or "No rate assumption configured",
                ),
            )
        )
        + "</aside>"
    )
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        + '<meta name="viewport" content="width=device-width,initial-scale=1">'
        + '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; form-action &#39;none&#39;">'
        + "<title>Tool Loop · Run "
        + _escape(result.get("run_id", "report"))
        + "</title><style>"
        + _CSS
        + '</style></head><body><main><div class="topline"><span class="brand">TOOL LOOP / MISSION CONTROL</span><span class="topnote">RAW SDK · BOUNDED EXECUTION · LOCAL EVIDENCE</span></div>'
        + '<div class="hero"><div><h1>Every decision.<br>One inspectable run.</h1><p class="subtitle">A trace of model calls, parallel tool execution, recovery, and the final outcome.</p></div><span class="status '
        + ("success" if success else "error")
        + '">'
        + _escape(status.upper().replace("_", " "))
        + '</span></div><p class="run-id">RUN '
        + _escape(result.get("run_id", "—"))
        + "</p>"
        + provenance
        + '<div class="metrics">'
        + metrics
        + '</div><section class="panel"><div class="section-head"><h2>Final response</h2><p class="caption">End reason: '
        + _escape(result.get("end_reason", result.get("status", "unknown")))
        + '</p></div><div class="final-response">'
        + _escape(result.get("final_text") or "No final response was produced.")
        + '</div></section><section class="panel"><div class="section-head"><h2>Execution waterfall</h2><p class="caption">Model calls in amber · tools in mint · errors in rose</p></div>'
        + _waterfall(spans)
        + '</section><div class="two-col"><section class="panel"><div class="section-head"><h2>Tool outcomes</h2><p class="caption">Expand to inspect the returned value or structured error.</p></div>'
        + _tool_results(tools)
        + '</section><section class="panel"><div class="section-head"><h2>Runtime decisions</h2><p class="caption">Ordered events expose limits, retries, and recovery decisions.</p></div>'
        + _events(result.get("events") or [])
        + '</section></div><section class="panel"><div class="section-head"><h2>OpenTelemetry evidence</h2><p class="caption trace-note">Trace '
        + _escape(", ".join(trace_ids) or "—")
        + " · hierarchy follows parent span IDs</p></div>"
        + _trace_table(spans)
        + '</section><div class="footer"><span>Generated from recorded runtime data. No external resources.</span><span>This report contains model and tool output. Review before sharing.</span></div></main></body></html>'
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "report.html"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(document)
    return path
