# Architecture and decisions

The runtime owns execution policy. A model owns only proposals and text. A tool handler owns only its registered application behavior.

## State transitions

A run begins with a developer instruction and user prompt. Each iteration builds a `ModelRequest`, obtains a complete `ModelReply`, accounts for observed usage, validates the batch, executes permitted calls, and appends one structured result per call to the transcript. A nonempty final reply completes the run; an empty final reply is `empty_response`.

Provider output items are retained as protocol state in memory. They are not exported into results or traces. In particular, the Responses adapter replays reasoning items rather than attempting to reconstruct a transcript from assistant text alone.

Terminal reasons are `completed`, `empty_response`, `turn_budget`, `tool_budget`, `token_budget`, `repeated_tool_batch`, `protocol_error`, `provider_error`, and `deadline`. External cancellation records `cancelled` and then propagates `CancelledError` to the caller. Tool errors usually remain recoverable results rather than terminating the run.

## Scheduling invariants

1. A handler never receives arguments that failed parsing or schema validation.
2. Only `parallel_safe` tools may overlap. Other tools are barriers: prior read groups drain before the tool starts, and later groups wait for it.
3. Active handlers are bounded by `max_parallel`; a `TaskGroup` owns each read group and drains its children on cancellation.
4. Results enter the model transcript in proposal order, regardless of completion order.
5. Accepted proposals remain accounted for after a deadline. Finished results survive; in-flight calls are cancelled; calls that never started have an explicit `not_executed` outcome.
6. A duplicate call ID rejects its whole batch before any handler starts. IDs may not be reused within a run.
7. Tool retries use deep copies of the originally validated arguments. A failed handler cannot corrupt the next attempt by mutating its input.

Parallel safety and retry safety are separate declarations. A write tool is serial by default. Retrying requires `retry_safe=True`, `max_attempts > 1`, and an explicit `ToolTransientError`. A timeout is never automatically retried because the external effect may already have happened. Runtime state is per run; provider instances with state, such as the fixture provider, should not be shared across concurrent runs.

## Validation boundary

JSON parsing rejects duplicate keys, NaN/Infinity (including exponent overflow), invalid Unicode and excessive nesting. Arguments have a UTF-8 byte limit. Registered schemas are validated against Draft 2020-12, require an object root and support resolved local JSON Pointer references only. External references, IDs, anchors and dynamic references are intentionally unsupported: validation must not introduce hidden network access. Validation-engine failures become structured errors and never authorize a handler.

Tool outputs must serialize as finite JSON within the configured size. Oversized output yields an error rather than invalid truncated JSON. Tools are trusted application code: the output check happens after allocation, and a blocking handler can still stall the process. This is a cooperative async scheduler, not a code sandbox.

## Budgets and accounting

Default limits are eight completed model turns, 32 accepted tool proposals, four parallel handlers, 30 seconds per run, ten seconds per model attempt, two model attempts, 20,000 observed total tokens, 2,048 requested output tokens, 8 KiB arguments, 16 KiB tool outputs, depth 32, and two consecutive identical tool batches. The third matching batch stops before execution. Limits are explicit dataclass configuration rather than instructions the model may ignore. The live CLI raises the run/model timeout to 120/45 seconds.

A model turn is a successfully returned proposal/final response. Transient request attempts have their own bounded count and trace spans; SDK retries are disabled. Proposed calls rejected by schema count against the accepted-call budget. A batch that would exceed the budget is rejected as a whole.

Observed token accounting is soft. The runtime cannot know exact prompt tokens before a model request, guarantee how much a timed-out request costs, or refund a response that exceeds the remaining total. It caps requested output, accounts for returned usage and stops subsequent work. Use provider/project controls for a separate spend boundary. Cost estimates use explicitly supplied rates, handle cached tokens separately and exclude unknown usage or non-token fees.

## Observability and privacy

`agent.run` spans contain terminal reason, turn/proposal counts and aggregate usage/cost. `llm.call` spans contain provider/model, attempt, usage, cached tokens and estimated cost when configured. `tool.execute` spans show validation and total recovery latency; nested `tool.attempt` spans show every actual handler attempt. Async context propagation preserves parent IDs across concurrent tasks.

The instance-scoped OTel SDK exports completed spans synchronously to JSONL for simple inspection and HTML rendering. It does not change the process's global provider or require a collector. This favors determinism and local evidence over peak throughput; exporter overhead is included in the reference benchmark. Integrations can adapt the SDK pipeline for a collector, but no OTLP integration is claimed here.

Operational traces exclude payloads and exception bodies. Result objects retain values for the calling application; the live CLI redacts persisted values/final text by default. The offline committed examples contain only synthetic data. File permissions protect local artifacts on POSIX; callers still control directory permissions, terminal capture, downstream exporters and sharing.

## Why the demo is scripted

The offline provider is a deterministic protocol fixture, not an imitation of measured inference. It deliberately emits one schema error and then a correction. A read-only tool deliberately fails once. That makes recovery, cancellation and scheduling reproducible without credentials, cost, nondeterministic model output or internet access.

The benchmark checks returned evidence and recovery outcomes; a scripted final answer alone cannot make a failed run pass. Both configurations use fresh scenario state and identical scripts/delays, with only concurrency changed. It measures runtime behavior, not the probability that a live model will reach the diagnosis.

## Verification boundaries

Behavioral tests cover schema rejection, Unicode/nesting attacks, semaphore bounds, serial barriers, deterministic reduction, timeout cleanup, external cancellation, partial results, retry argument isolation, loop circuits, budgets, provider error mapping, actual SDK wire parsing through a mock transport, cost math, trace parentage, report escaping and live-artifact privacy. CI also executes the documented containers.

Live-model behavior, model-quality comparisons, prompt-injection robustness, production throughput, distributed quotas, durable recovery, persistent idempotency and untrusted-code isolation have not been established. This repository makes the local runtime contract inspectable and testable; those are separate engineering problems.
