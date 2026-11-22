# Temporary disconnect diagnostics

These sources investigate provider signals; they are not part of the release
gateway and do not replace its SQL/worker cancellation test. The
[measured report](../../../docs/plans/supabase/experiments/disconnect-signals.md)
records the actual runs, versions, limitations and cleanup.

`index.ts` is a standalone Supabase diagnostic function. Authenticate with the
existing installation's application key and pass a random UUID in
`X-Oshepherd-Probe`. It verifies the caller through `backend_info`, returns three
small NDJSON records with four-second gaps, and stores a bounded event-only
report after twelve seconds. The private object path is
`<cluster>/<probe-uuid>/<probe-uuid>/result.json` in `oshepherd-results`. Runtime
configuration uses the same server/cluster/schema variables as the Edge gateway.

To reproduce, copy it into a disposable function directory alongside a copy of
the current gateway's RPC/normalization dependencies, adjusting the relative RPC
import. Select the already authorized isolated project explicitly; do not change
the real `ollama` function. Run two concurrent authenticated requests with distinct
UUIDs: consume all records for the control, and close immediately after record
zero for the disconnect case. Read reports with the private operator credential,
then delete only those exact report objects and the temporary function. Keep
credentials in protected files, never shell arguments or printed output.

`asgi.py` wraps an existing ASGI app only when a UUID probe header is present. It
records ASGI version, static route category, event names, timings and byte counts;
it never records headers, bodies, query strings or exception messages. An optional
async reporter receives the same bounded trace under a five-second cleanup
shield. Preserve the gateway's existing authentication. Configure that reporter
for only the chosen run marker and an owned private destination; do not add a
public report-writing endpoint. Test the real hosted stream and its SQL terminal
state, retrieve/delete reports, then remove the wrapper and restore the normal
gateway deployment. A normal-response control is required because servers may
emit `http.disconnect` after ordinary response completion.

The qualification controllers and raw logs remain in ignored private state.
Provider log streams can omit aborted-request messages; absence of a log is not
proof that an ASGI event was absent. Record any diagnostics-induced lifecycle
differences, including the Edge probe's bounded background retention. These
results investigate signals only; they do not broaden the advertised workload or
make either hosting profile qualified.
