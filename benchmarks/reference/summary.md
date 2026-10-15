# Offline protocol benchmark

Measured wall-clock latency on synthetic tools and scripted model replies. No live model calls.

| Configuration | Samples | Protocol pass | p50 ms | p95 ms |
| --- | ---: | ---: | ---: | ---: |
| sequential | 30 | 100% | 304.36 | 307.32 |
| parallel | 30 | 100% | 192.13 | 195.19 |

p50 speedup: **1.58×**. Results depend on this workload and host.

Token counts and price projections are synthetic fixtures. Live model quality, real cost, and TTFT are not measured.

See summary.json for configuration, environment, source digest and assumptions; runs.jsonl contains every measured sample.
