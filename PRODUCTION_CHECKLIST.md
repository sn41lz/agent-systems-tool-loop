# Standard Production Checklist

**Status: planning checklist.** The boxes below are acceptance criteria for future implementation, not a claim that files, commands, security controls, tests, or measurements exist today.

## Reproducible setup

- [ ] Add a `docker-compose.yml`, `Makefile`, documented prerequisites, pinned dependencies, and a credential-free `.env.example`.
- [ ] Make `make demo` prepare its environment and run a deterministic offline demonstration in one command.
- [ ] Make `make test` prepare its environment and run meaningful offline regression checks in one command.
- [ ] Offer live-model runs separately, with explicit provider configuration, expected costs, and no committed credentials.

## Inspectable mechanics

- [ ] In the tool loop, context window, hierarchical memory, and multi-agent orchestration repositories, implement control loops, state reducers, context compaction, memory policies, and coordination directly against raw model SDKs; do not use LangChain, LlamaIndex, or another orchestration framework for the core mechanics.
- [ ] Document architecture, execution budgets, tradeoffs, failure modes, and the boundaries of what has actually been verified.

## Observability and benchmarks

- [ ] Emit OpenTelemetry spans for every model call and tool execution, linked to the request/run trace.
- [ ] Record model/provider configuration, outcome, retry count, duration, input/output/cached tokens where available, and estimated cost without recording secrets or sensitive payloads by default.
- [ ] Aggregate p50/p95 request latency and streaming TTFT, token usage, and estimated USD per 1,000 requests. Mark unavailable metrics as unavailable and disclose pricing date, currency, and assumptions.
- [ ] Publish baseline-versus-optimized results using the same versioned workload, configuration, environment, concurrency, sample counts, and declared quality checks. Include raw results and exact reproduction instructions.
- [ ] Keep unmeasured cells labeled **Not yet measured**; distinguish targets and candidate failure modes from observations and verified behavior.

## Regression and delivery

- [ ] Add deterministic offline fixtures that check observable behavior and meaningful failure cases.
- [ ] Add CI for the documented checks, with optional live-model evaluation kept explicit and separately configured.
- [ ] Document project-specific acceptance criteria and any unsupported environments or residual limitations.
