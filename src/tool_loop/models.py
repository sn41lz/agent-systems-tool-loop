"""Contracts shared by providers, tools, and the control loop.

The provider proposes work. The runtime alone decides whether it may execute.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


class ProviderError(Exception):
    """An unrecoverable provider/protocol failure (message must be safe to log)."""


class ProviderTransientError(ProviderError):
    """A provider failure the runtime may retry within its attempt budget."""


class ToolTransientError(Exception):
    """An explicitly retryable tool failure; only retry-safe tools are retried."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def __post_init__(self) -> None:
        values = (self.input_tokens, self.output_tokens, self.cached_input_tokens)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Token usage must contain nonnegative integers")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("Cached input tokens cannot exceed input tokens")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
        )


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelReply:
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    # All raw provider output items, including encrypted reasoning, are fed back
    # into the next request. They are never included in exported run artifacts.
    output_items: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ModelRequest:
    items: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...]
    max_output_tokens: int


class Provider(Protocol):
    async def generate(self, request: ModelRequest) -> ModelReply: ...


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[Any]]
    timeout_s: float = 2.0
    max_attempts: int = 1
    retry_safe: bool = False
    # Explicit opt-in. Unsafe tools are serial barriers in proposal order.
    parallel_safe: bool = False


@dataclass(frozen=True)
class Limits:
    max_turns: int = 8
    max_tool_calls: int = 32
    max_parallel: int = 4
    run_timeout_s: float = 30.0
    model_timeout_s: float = 10.0
    max_model_attempts: int = 2
    max_tool_output_bytes: int = 16_384
    max_argument_bytes: int = 8_192
    max_json_depth: int = 32
    max_total_tokens: int = 20_000
    max_output_tokens: int = 2_048
    max_repeated_batch: int = 2
    retry_backoff_s: float = 0.025

    def __post_init__(self) -> None:
        for name in (
            "max_turns",
            "max_tool_calls",
            "max_parallel",
            "max_model_attempts",
            "max_tool_output_bytes",
            "max_argument_bytes",
            "max_json_depth",
            "max_total_tokens",
            "max_output_tokens",
            "max_repeated_batch",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("run_timeout_s", "model_timeout_s", "retry_backoff_s"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0 or (name != "retry_backoff_s" and value == 0):
                raise ValueError(f"{name} must be finite and positive (backoff may be zero)")


@dataclass(frozen=True)
class Pricing:
    """Explicit caller-supplied USD per million tokens; never a live price quote."""

    input_per_million: float
    output_per_million: float
    cached_input_per_million: float
    label: str

    def __post_init__(self) -> None:
        values = (self.input_per_million, self.output_per_million, self.cached_input_per_million)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Prices must be finite and nonnegative")

    def cost(self, usage: Usage) -> float:
        return (
            (usage.input_tokens - usage.cached_input_tokens) * self.input_per_million
            + usage.cached_input_tokens * self.cached_input_per_million
            + usage.output_tokens * self.output_per_million
        ) / 1_000_000


@dataclass
class ToolResult:
    call_id: str
    name: str
    ok: bool
    value: Any = None
    error: dict[str, Any] | None = None
    attempts: int = 0
    duration_ms: float = 0.0
    cached: bool = False

    def model_output(self) -> str:
        import json

        body = (
            {"ok": self.ok, "result": self.value} if self.ok else {"ok": False, "error": self.error}
        )
        return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


@dataclass
class RunResult:
    run_id: str
    status: str = "running"
    final_text: str = ""
    turns: int = 0
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float | None = None
    pricing_label: str | None = None
    duration_ms: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
