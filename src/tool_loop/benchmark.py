"""Paired, reproducible protocol benchmarks; not a measure of model intelligence."""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tool_loop.models import Limits, Pricing, RunResult
from tool_loop.runtime import Agent
from tool_loop.scenarios import get_scenario
from tool_loop.telemetry import Telemetry

# Deliberately fictional rates. The offline provider makes zero paid API calls.
ILLUSTRATIVE_PRICING = Pricing(
    1.0, 4.0, 0.25, "Illustrative USD/million: input=1, output=4, cached=0.25; not provider pricing"
)


def percentile(values: list[float], quantile: float) -> float:
    if not values or not 0 <= quantile <= 1:
        raise ValueError("Percentiles require observations and quantile in [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def protocol_passed(result: RunResult) -> bool:
    expected_evidence = ["topology-01", "deploy-02", "metrics-03", "logs-04"]
    evidence = [
        item.value.get("evidence_id")
        for item in result.tool_results
        if item.ok and isinstance(item.value, dict)
    ]
    return (
        result.status == "completed"
        and result.turns == 3
        and result.tool_calls == 5
        and evidence == expected_evidence
        and len(result.tool_results) == 5
        and result.tool_results[3].error is not None
        and result.tool_results[3].error["code"] == "schema_validation"
        and result.tool_results[3].attempts == 0
        and result.tool_results[4].attempts == 2
    )


async def benchmark(
    runs: int = 30, *, output: Path, delay_scale: float = 1.0, warmups: int = 2
) -> dict[str, Any]:
    if runs < 3 or warmups < 0:
        raise ValueError("Use at least three measured runs and nonnegative warmups")
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with Telemetry(output) as telemetry:
        for index in range(-warmups, runs):
            # Alternate pair order to reduce persistent first/second-run bias.
            configurations = [("sequential", 1), ("parallel", 4)]
            if index % 2:
                configurations.reverse()
            for label, parallelism in configurations:
                scenario = get_scenario(delay_scale=delay_scale)
                result = await Agent(
                    scenario.provider,
                    scenario.tools,
                    limits=Limits(max_parallel=parallelism),
                    telemetry=telemetry,
                    pricing=ILLUSTRATIVE_PRICING,
                ).run(scenario.prompt)
                if index >= 0:
                    rows.append(
                        {
                            "pair": index,
                            "configuration": label,
                            "max_parallel": parallelism,
                            "run_id": result.run_id,
                            "duration_ms": result.duration_ms,
                            "status": result.status,
                            "protocol_passed": protocol_passed(result),
                            "turns": result.turns,
                            "tool_calls": result.tool_calls,
                            "usage": asdict(result.usage),
                            "illustrative_cost_usd": result.cost_usd,
                        }
                    )
    groups: dict[str, Any] = {}
    for label in ("sequential", "parallel"):
        samples = [row for row in rows if row["configuration"] == label]
        groups[label] = {
            "samples": len(samples),
            "protocol_pass_rate": sum(row["protocol_passed"] for row in samples) / len(samples),
            "latency_p50_ms": percentile([row["duration_ms"] for row in samples], 0.50),
            "latency_p95_ms": percentile([row["duration_ms"] for row in samples], 0.95),
            "synthetic_tokens_per_run": samples[0]["usage"],
            "illustrative_usd_per_1000_runs": sum(row["illustrative_cost_usd"] for row in samples)
            / len(samples)
            * 1000,
        }
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "workload": "synthetic checkout canary incident; scripted replies + async in-memory tools",
        "workload_source_sha256": hashlib.sha256(
            Path(__file__).with_name("scenarios.py").read_bytes()
        ).hexdigest(),
        "source_sha256": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in (
                "runtime.py",
                "providers.py",
                "models.py",
                "benchmark.py",
                "scenarios.py",
                "telemetry.py",
            )
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "method": {
            "paired_runs_per_configuration": runs,
            "discarded_warmup_pairs": warmups,
            "pair_order": "alternating",
            "percentile": "linear interpolation at (n-1)*q",
            "delay_scale": delay_scale,
            "limits": asdict(Limits()),
            "only_changed_setting": "max_parallel: 1 → 4",
        },
        "pricing_assumption": asdict(ILLUSTRATIVE_PRICING),
        "actual_model_api_calls": 0,
        "actual_model_api_cost_usd": 0,
        "live_model_quality": "Not measured; final replies are scripted, not inferred",
        "streaming_ttft": "Not measured; this runtime uses non-streaming Responses",
        "groups": groups,
        "p50_speedup": groups["sequential"]["latency_p50_ms"]
        / groups["parallel"]["latency_p50_ms"],
        "all_protocol_checks_passed": all(row["protocol_passed"] for row in rows),
    }
    (output / "runs.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    )
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output / "latency.svg").write_text(_chart(summary))
    (output / "summary.md").write_text(_markdown(summary))
    return summary


def _markdown(summary: dict[str, Any]) -> str:
    groups = summary["groups"]
    lines = [
        "# Offline protocol benchmark",
        "",
        "Measured wall-clock latency on synthetic tools and scripted model replies. No live model calls.",
        "",
        "| Configuration | Samples | Protocol pass | p50 ms | p95 ms |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, group in groups.items():
        lines.append(
            f"| {name} | {group['samples']} | {group['protocol_pass_rate']:.0%} | {group['latency_p50_ms']:.2f} | {group['latency_p95_ms']:.2f} |"
        )
    lines.extend(
        [
            "",
            f"p50 speedup: **{summary['p50_speedup']:.2f}×**. Results depend on this workload and host.",
            "",
            "Token counts and price projections are synthetic fixtures. Live model quality, real cost, and TTFT are not measured.",
            "",
            "See summary.json for configuration, environment, source digest and assumptions; runs.jsonl contains every measured sample.",
            "",
        ]
    )
    return "\n".join(lines)


def _chart(summary: dict[str, Any]) -> str:
    groups = summary["groups"]
    largest = max(group["latency_p95_ms"] for group in groups.values()) or 1
    bars = []
    for index, (label, group) in enumerate(groups.items()):
        y = 105 + index * 105
        for metric, color, offset in [("p50", "#65e1c0", 0), ("p95", "#7d9dff", 30)]:
            value = group[f"latency_{metric}_ms"]
            width = value / largest * 380
            bars.append(
                f'<rect x="195" y="{y + offset}" width="{width:.2f}" height="20" rx="5" fill="{color}"/><text x="{205 + width:.2f}" y="{y + offset + 15}" fill="#e5edf7" font-size="13">{metric} {value:.1f} ms</text>'
            )
        bars.append(f'<text x="28" y="{y + 25}" fill="#e5edf7" font-size="16">{label}</text>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="760" height="350" viewBox="0 0 760 350" role="img" aria-labelledby="title desc"><title id="title">Sequential versus parallel offline tool-loop latency</title><desc id="desc">Measured synthetic protocol benchmark. Model replies and token counts are scripted; no live inference.</desc><rect width="760" height="350" rx="16" fill="#101827"/><g font-family="system-ui,sans-serif"><text x="28" y="40" fill="#fff" font-size="22" font-weight="700">Less waiting. Same execution contract.</text><text x="28" y="67" fill="#9eacc1" font-size="13">Synthetic incident drill · paired runs · measured wall-clock latency</text>'
        + "".join(bars)
        + f'<text x="28" y="324" fill="#9eacc1" font-size="13">{summary["method"]["paired_runs_per_configuration"]} samples/configuration · p50 speedup {summary["p50_speedup"]:.2f}× · live model quality unmeasured</text></g></svg>\n'
    )
