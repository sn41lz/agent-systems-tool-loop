# Raw Agent Tool Loop

An inspectable agent control loop built directly on a model SDK, with explicit tool contracts and bounded execution.

**Status: planning — implementation has not started.** This README describes intended scope and acceptance criteria. It does not establish working behavior, completed tests, security guarantees, or measured performance.

Part of an eight-repository agent-systems portfolio.

## Planned scope

- Model-to-tool dispatch with JSON Schema validation and structured tool results.
- Concurrent independent tools with bounded parallelism, cancellation, and timeouts.
- Recoverable tool errors, bounded model-assisted correction, and explicit stop reasons.

## Planned setup and observability

The implementation is intended to provide a `docker-compose.yml` and `Makefile` so a reviewer can use `make demo` for a deterministic offline demonstration and `make test` for offline regression checks. **These commands and their supporting files are not implemented yet.** Live-model runs will be optional and separately documented with configuration and cost assumptions.

Every model call and tool execution is intended to emit an OpenTelemetry span. Planned aggregate reporting includes token usage, estimated USD per 1,000 requests, p50/p95 latency, and p50/p95 time to first token (TTFT) where streaming applies. Offline fixtures can verify instrumentation and behavior; live-provider performance requires separate measurements.

The core mechanics will use raw model SDKs with explicit control flow; no LangChain, LlamaIndex, or other orchestration framework will implement them.

See [PRODUCTION_CHECKLIST.md](PRODUCTION_CHECKLIST.md) for shared acceptance criteria.

## Proposed architecture and tradeoffs

A provider adapter feeds an explicit state machine. A tool registry validates calls before a bounded executor runs them, and a reducer appends structured results to the next model input. Execution and retry budgets belong to the loop, not to the model.

Parallel tools can lower latency but complicate ordering, cancellation, and side effects. Error correction can improve completion rates while adding model calls and cost. The initial scope should favor deterministic, side-effect-free tools.

## Roadmap and acceptance criteria

Complete the shared reproducibility and observability checklist, then implement and verify these project-specific criteria:

- [ ] Reject malformed and unknown tool calls without executing them; demonstrate structured recovery with fixed fixtures.
- [ ] Execute independent tools concurrently while preserving deterministic result association.
- [ ] Enforce call, retry, token, and wall-clock budgets with observable terminal reasons.
- [ ] Compare sequential and concurrent execution on the same workload, reporting completion rate alongside latency and cost.

## Candidate failure modes

These are design risks to investigate, **not tested or observed findings**:

- The model repeats an invalid call or loops without making progress.
- A timed-out tool completes late, or one failed task prevents cancellation of its siblings.
- Retries duplicate side effects, or parallel execution violates an unstated dependency.

The implementation will add reproduction cases, actual outcomes, and mitigations as evidence becomes available.

## Baseline versus optimized metrics

No benchmark has been run. The following comparison is planned:

- **Baseline:** Sequential validated tool execution with a fixed prompt and identical workload.
- **Optimized:** Bounded parallel execution and capped correction, with budgets held constant.

| Metric | Baseline | Optimized |
| --- | --- | --- |
| Input/output/total tokens per request | Not yet measured | Not yet measured |
| Estimated USD per 1,000 requests | Not yet measured | Not yet measured |
| End-to-end latency p50 | Not yet measured | Not yet measured |
| End-to-end latency p95 | Not yet measured | Not yet measured |
| Streaming TTFT p50, where applicable | Not yet measured | Not yet measured |
| Streaming TTFT p95, where applicable | Not yet measured | Not yet measured |
| Project-specific quality and failure rate | Not yet measured | Not yet measured |

Future results must identify the workload revision, model/provider configuration, environment, concurrency, sample counts, and quality criteria. Cost estimates must disclose pricing date and assumptions. Report the complete workload, including errors and retries, and retain raw results so baseline and optimized runs can be reproduced. Do not substitute target values for measurements.

## Portfolio

- [Raw Agent Tool Loop](https://github.com/sn41lz/agent-systems-tool-loop)
- [Context Window Engine](https://github.com/sn41lz/agent-systems-context-window)
- [Hierarchical Agent Memory](https://github.com/sn41lz/agent-systems-hierarchical-memory)
- [Multi-Agent Orchestrator](https://github.com/sn41lz/agent-systems-multi-agent-orchestrator)
- [Agent Evaluation Harness](https://github.com/sn41lz/agent-systems-eval-harness)
- [Secure Code Sandbox](https://github.com/sn41lz/agent-systems-secure-sandbox)
- [Durable Agent Runtime](https://github.com/sn41lz/agent-systems-durable-runtime)
- [Agent Platform API and SDKs](https://github.com/sn41lz/agent-systems-api-sdk)
