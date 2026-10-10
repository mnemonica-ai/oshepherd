# Local worker recovery and control evidence

Verified October 10, 2026 against the isolated local Supabase stack. These are
deterministic lifecycle checks, not hosted load qualification or a long-running soak.

The worker now separates its inference pool (64 connections) from metadata
(four connections). Each metadata fetch has a five-second budget; a complete
inventory cycle has a 25-second budget. Invalid model records preserve the last
good inventory until its SQL freshness expires. Heartbeat and Auth refresh
continue independently of silent inference and stalled metadata.

Eleven recovery cases in `tests/integration/supabase/test_recovery.py` cover:

- Twenty simultaneous real TCP inference reads while metadata still completes.
- A stalled metadata socket timing out, closing and allowing reuse.
- Real Auth refresh during silent inference.
- Eight accepted jobs at capacity, with the ninth rejected and no duplicate execution.
- Worker generation replacement before and after durable output, proving retry/fencing rules.
- A blocked ASGI reader cancelling with `reader_stalled` and stopping worker reads.
- Null tags, invalid model records and null/string capability lists during active inference, followed by recovery.

The ASGI slow-reader case controls the application's send boundary. It does not
prove hosted TCP backpressure. FastAPI's separate actual socket cancellation test
also passes. The Deno gateway suite has 44 passing contracts, including an unread
stream stall and a reader waiting for silent output.

The strict real Edge HTTP reader-disconnect test still fails: SQL records
`succeeded` after the client closes. Inspection of the installed CLI proxy found
two fetch calls, one signal reference used for JWT/JWKS retrieval, and no explicit
AbortController or response-completion wiring. This points to the forwarding
boundary; source counts alone cannot exclude implicit native abort propagation.
The official runtime example is different from the installed CLI main service.
No runtime patch, weakened assertion or substitute FastAPI/Deno proof closes this gate.

Run with the existing isolated stack:

```sh
uv run --locked pytest -q tests/integration/supabase/test_recovery.py --tb=short
uv run --locked pytest -q tests/integration/supabase/test_edge.py -k first_record --tb=short
uv run --locked python -m scripts.inspect_edge_runtime
```

Runtime source and logs remain private under `.oshepherd/local`. The inspection
prints only image, digest, byte size and structural counts. Hosted Free/Hobby
limits, downstream disconnect propagation, soak and measured load/cost remain open.
