"""Credential-free demos by default; live model access is an explicit subcommand."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from tool_loop.benchmark import ILLUSTRATIVE_PRICING, benchmark
from tool_loop.models import Limits, Pricing, ProviderError
from tool_loop.providers import OpenAIProvider
from tool_loop.report import write_report
from tool_loop.runtime import Agent
from tool_loop.scenarios import get_scenario
from tool_loop.telemetry import Telemetry


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="An inspectable raw-SDK agent loop. Start with 'demo'."
    )
    commands = root.add_subparsers(dest="command", required=True)
    demo = commands.add_parser(
        "demo", help="Run a synthetic incident or circuit-breaker drill; no credentials"
    )
    demo.add_argument("--scenario", choices=("incident", "loop"), default="incident")
    demo.add_argument("--parallel", type=int, default=4)
    demo.add_argument("--output", type=Path, default=Path("artifacts/demo"))
    bench = commands.add_parser(
        "benchmark", help="Compare sequential vs parallel scheduling on the same offline workload"
    )
    bench.add_argument("--runs", type=int, default=30)
    bench.add_argument("--output", type=Path, default=Path("artifacts/benchmark"))
    live = commands.add_parser(
        "live", help="Explicitly call OpenAI using synthetic read-only incident tools"
    )
    live.add_argument("--model", required=True, help="A model ID available to your OpenAI project")
    live.add_argument("--output", type=Path, default=Path("artifacts/live"))
    live.add_argument(
        "--include-content",
        action="store_true",
        help="Include final text/tool results in local artifacts (traces always omit them)",
    )
    live.add_argument(
        "--input-rate", type=float, help="Caller-supplied USD per million uncached input tokens"
    )
    live.add_argument(
        "--output-rate", type=float, help="Caller-supplied USD per million output tokens"
    )
    live.add_argument(
        "--cached-rate", type=float, help="Caller-supplied USD per million cached input tokens"
    )
    live.add_argument(
        "--price-label", help="Pricing source/date or assumption label; required with rates"
    )
    return root


async def _run(args: argparse.Namespace) -> int:
    if args.command == "benchmark":
        summary = await benchmark(args.runs, output=args.output)
        print((args.output / "summary.md").read_text())
        print(f"Raw results: {args.output / 'runs.jsonl'}")
        return 0 if summary["all_protocol_checks_passed"] else 1
    live = args.command == "live"
    if live and not os.environ.get("OPENAI_API_KEY"):
        print(
            "Set OPENAI_API_KEY before using the explicit live command. The demo needs no key.",
            file=sys.stderr,
        )
        return 2
    pricing: Pricing | None = ILLUSTRATIVE_PRICING
    if live:
        rates = (args.input_rate, args.output_rate, args.cached_rate, args.price_label)
        if any(value is not None for value in rates) and not all(
            value is not None for value in rates
        ):
            raise ValueError(
                "Provide all three rates and --price-label together, or omit all for unpriced usage"
            )
        pricing = Pricing(*rates) if all(value is not None for value in rates) else None
    scenario = get_scenario("incident" if live else args.scenario)
    limits = Limits(
        max_parallel=4 if live else args.parallel,
        run_timeout_s=120 if live else 30,
        model_timeout_s=45 if live else 10,
    )
    provider = OpenAIProvider(args.model) if live else scenario.provider
    try:
        with Telemetry(args.output) as telemetry:
            result = await Agent(
                provider, scenario.tools, limits=limits, telemetry=telemetry, pricing=pricing
            ).run(scenario.prompt)
            document = result.to_dict()
            document["scenario"] = scenario.title
            document["usage_label"] = provider.usage_label
            document["data_source"] = "synthetic in-memory incident evidence"
            document["model_source"] = (
                "live OpenAI model" if live else "scripted protocol fixture; no inference"
            )
            document["model"] = provider.model
            if live and not args.include_content:
                document["final_text"] = (
                    "Content omitted from artifacts; use --include-content to opt in."
                )
                for item in document["tool_results"]:
                    item["value"] = None
                    if item["error"]:
                        item["error"].pop("details", None)
            _write_json(args.output / "result.json", document)
            write_report(document, telemetry.events, args.output)
    finally:
        if isinstance(provider, OpenAIProvider):
            await provider.aclose()
    print(result.final_text or f"Run stopped: {result.status}")
    print(
        f"\n{result.status} · {result.turns} model turns · {result.tool_calls} tool proposals · {result.duration_ms:.1f} ms"
    )
    print(f"Usage: {result.usage.total_tokens} tokens ({provider.usage_label})")
    print(f"Report: {args.output / 'report.html'}")
    expected = "repeated_tool_batch" if not live and args.scenario == "loop" else "completed"
    return 0 if result.status == expected else 1


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except ProviderError:
        print(
            "Provider configuration is invalid. Check model, credentials and endpoint settings.",
            file=sys.stderr,
        )
        return 2
    except (ValueError, OSError) as exc:
        print(f"Configuration/output error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Cancelled; in-flight cooperative tools were drained.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
