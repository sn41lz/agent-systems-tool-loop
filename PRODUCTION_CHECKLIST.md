# Production checklist and evidence

This checklist records the implemented local runtime and review path. Broader production guarantees remain scoped in [architecture and decisions](docs/architecture.md).

- [x] Core loops, scheduling, error recovery and result reduction are implemented directly in [runtime.py](src/tool_loop/runtime.py); raw model SDK adapter only.
- [x] Single-command offline demo and test targets: [Makefile](Makefile), [docker-compose.yml](docker-compose.yml), [Dockerfile](Dockerfile). A fresh build downloads pinned dependencies; execution has no network.
- [x] Exact, SHA-256-checked runtime, development and build locks; private `.venv` setup; credential-free [.env.example](.env.example).
- [x] Real OTel spans for every model request, tool proposal and handler attempt, with async parent relationships and local JSONL evidence.
- [x] Returned token/cache usage, explicit estimated cost, per-1,000 projection, model/tool duration distributions, and paired end-to-end p50/p95 reporting. Unknown values remain unknown.
- [x] [Architecture, tradeoffs and failure modes](docs/architecture.md), including cooperative cancellation, soft token accounting and ambiguous external effects.
- [x] Measured [baseline versus parallel results](benchmarks/reference/summary.json) with [raw samples](benchmarks/reference/runs.jsonl), matching workload, quality checks, environment/configuration and source hashes.
- [x] Offline behavioral, fault-injection, SDK-contract and artifact-privacy tests; typed package, lint, formatting and wheel/source builds.
- [x] [GitHub Actions](.github/workflows/ci.yml) executes Python 3.12–3.14 checks and actual Docker review paths without provider credentials.
- [x] Portable [HTML report example](docs/examples/report.html), inspectable trace and documented commands to reproduce it.
- [ ] Live model quality, pricing, latency and streaming TTFT measurements. Offline tokens and rate examples are synthetic; the model adapter is non-streaming.
- [ ] Production load tests, distributed durability/idempotency and isolation of untrusted generated code. These are outside this repository's runtime contract.
