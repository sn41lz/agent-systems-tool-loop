"""Small, instance-scoped OpenTelemetry tracing with a portable JSONL export.

Each runtime owns its tracer provider. Importing or constructing this module never
changes the application's global OpenTelemetry provider, which also makes two
independent runs safe to test in the same interpreter. Context propagation uses
OpenTelemetry's normal context variables and follows asyncio task creation.

Callers should record operational metadata (names, counts, durations, outcomes),
never prompts, tool arguments, tool results, credentials, or exception messages.
The exporter intentionally omits exception event payloads and status descriptions:
SDK-generated exception messages and stack traces can contain user input.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, TextIO

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace import Span as SDKSpan
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)


class _RunExporter(SpanExporter):
    """Finish-ordered records, flushed after every span for crash inspection."""

    def __init__(self, events: list[dict[str, Any]], output_dir: Path | None) -> None:
        self._events = events
        self._lock = Lock()
        self._file: TextIO | None = None
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            # Trace metadata should not become world-readable on permissive umasks.
            descriptor = os.open(
                output_dir / "traces.jsonl", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
            )
            os.fchmod(descriptor, 0o600)
            self._file = os.fdopen(descriptor, "w", encoding="utf-8")

    def export(self, spans: Any) -> SpanExportResult:
        with self._lock:
            for span in spans:
                record = self._record(span)
                self._events.append(record)
                if self._file is not None:
                    self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
            if self._file is not None:
                self._file.flush()
        return SpanExportResult.SUCCESS

    @staticmethod
    def _record(span: ReadableSpan) -> dict[str, Any]:
        context = span.get_span_context() or trace.INVALID_SPAN_CONTEXT
        start_ns = span.start_time or 0
        end_ns = span.end_time or start_ns
        return {
            "name": span.name,
            "trace_id": f"{context.trace_id:032x}",
            "span_id": f"{context.span_id:016x}",
            "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
            "start_time_ms": start_ns / 1_000_000,
            "end_time_ms": end_ns / 1_000_000,
            "duration_ms": max(0, end_ns - start_ns) / 1_000_000,
            "attributes": dict(span.attributes or {}),
            "status": span.status.status_code.name,
        }

    def shutdown(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.flush()
                self._file.close()
                self._file = None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        with self._lock:
            if self._file is not None:
                self._file.flush()
        return True


class Telemetry:
    """Collect real OTel spans in memory and optionally in ``traces.jsonl``.

    ``span`` is a synchronous context manager, including inside async functions.
    Its yielded span supports the standard OTel ``set_attribute``,
    ``record_exception``, and ``set_status`` methods. Errors escaping a context
    automatically mark the span as ERROR. Successful spans are explicitly OK.
    Call ``close`` after all child tasks have completed to flush and release files.
    """

    def __init__(self, output_dir: Path | None = None, service_name: str = "tool-loop") -> None:
        self.events: list[dict[str, Any]] = []
        self._provider = TracerProvider(
            resource=Resource.create({"service.name": service_name}),
            shutdown_on_exit=False,
        )
        self._provider.add_span_processor(
            SimpleSpanProcessor(_RunExporter(self.events, output_dir))
        )
        self._tracer = self._provider.get_tracer("tool_loop", "1.0.0")
        self._closed = False

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None) -> Iterator[trace.Span]:
        if self._closed:
            raise RuntimeError("Telemetry is closed")
        with self._tracer.start_as_current_span(name, attributes=attributes) as span:
            try:
                yield span
            except BaseException as exc:
                # OTel's default context manager catches Exception, while task
                # cancellation and process interrupts inherit BaseException.
                # They must not look like successful/unknown operations.
                span.set_status(trace.Status(trace.StatusCode.ERROR))
                if isinstance(exc, asyncio.CancelledError):
                    span.set_attribute("error.type", "cancelled")
                raise
            else:
                if isinstance(span, SDKSpan) and span.status.status_code is trace.StatusCode.UNSET:
                    span.set_status(trace.Status(trace.StatusCode.OK))

    def close(self) -> None:
        """Flush completed spans; safe to call repeatedly."""
        if not self._closed:
            self._closed = True
            self._provider.force_flush()
            self._provider.shutdown()

    def __enter__(self) -> Telemetry:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
