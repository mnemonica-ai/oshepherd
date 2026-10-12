# Hosted disconnect signal investigation

Recorded October 11, 2026 on the existing authorized isolated Free/Hobby targets.
This investigation does not close the strict cancellation gate or qualify either
profile. No production gateway or applied migration source was changed.

## Supabase Edge

The pinned local runtime is `v1.74.3`, source commit
`47d04fdd22e33ea3fd904576cf3248d963d903a9`. Its
[serve override](https://github.com/supabase/edge-runtime/blob/47d04fdd22e33ea3fd904576cf3248d963d903a9/ext/runtime/js/http.js)
passes handler info containing `remoteAddr`, without `completed`. Its
[legacy response writer](https://github.com/supabase/edge-runtime/blob/47d04fdd22e33ea3fd904576cf3248d963d903a9/ext/runtime/js/01_http.js)
cancels the body/aborts the request when response writing fails. This source
explains why the optional completion hook is unavailable locally; it does not
identify the exact build running on the hosted provider.

An authenticated temporary hosted function returned three diagnostic NDJSON
records with four-second gaps. A control consumed the complete response; the
disconnect case closed immediately after record zero. A bounded background
diagnostic wrote only event names, times and capability booleans to private
Storage, independently of client/log delivery.

| Observation | Client closes after first record | Complete-response control |
| --- | --- | --- |
| Client duration | 2.01 seconds | 10.02 seconds |
| Handler completion promise | Unavailable | Unavailable |
| First pull | 0.001 seconds | 0.001 seconds |
| Later pulls | 4.002 and 8.004 seconds | 4.002 and 8.003 seconds |
| Stream closes | 8.004 seconds | 8.003 seconds |
| Request abort | 8.006 seconds, after stream close | Not observed |
| Stream cancel callback | Not observed | Not observed |

Handler and client durations have different starting points. The probe uses
`EdgeRuntime.waitUntil` solely to retain the diagnostic report; it is not a
replacement for the real inference/SQL cancellation regression. The observed
hosted behavior independently shows continued response consumption after the
reader closes and an abort delivered only after completion. Both private report
objects and the temporary function were removed. The normal Edge gateway remains.

## Vercel Python

The installed CLI is 50.1.3 and its bundled `@vercel/python` is 6.1.5. Its local
`vc_init.py` launches Uvicorn behind a provider proxy; that installed adapter is
source evidence, not proof of the deployed builder version. Actual hosted ASGI
scope reports version 3.0/spec 2.3. Starlette therefore runs its receive-based
disconnect listener; the failure is not caused by selecting its spec-2.4
send-error-only branch.

A temporary event-only ASGI wrapper surrounded the unchanged gateway during the
strict real SQL/Auth/worker probe. Provider log streaming captured normal
requests but omitted the aborted request. Missing logs were not treated as proof
that a signal was absent. A second run wrote bounded event-only reports to the
same approved private Supabase project, using the already configured gateway
credential. Its reporter accepted only that run's random marker and generate
route. Three report objects were retrieved and deleted.

The delayed streaming request's trace was:

| Event | Seconds from ASGI entry |
| --- | --- |
| Receive request body | 0.201 |
| Send response headers | 0.644 |
| Send first nonterminal record, 395 bytes | 1.064 |
| Send terminal record, 373 bytes | 4.970 |
| Send final empty body | 4.970 |
| Receive `http.disconnect` | 4.970 |

The client received the first record and closed at 1.27 seconds from its request
start. SQL ended `succeeded`. No send error or earlier disconnect appeared in the
trace. The normal streaming fixture also received `http.disconnect` after its
final body, so a disconnect event by itself does not imply an aborted response.

Both diagnostic deployments were followed by successful restoration of the
normal gateway; the temporary wrapper module was removed from the local bundle.
Routes, exact NDJSON, auth and the 4,200,347-byte private download passed during
the traced run. The strict disconnect assertion still exited unsuccessfully.

## Consequence and remaining decision

The measured gateways receive their cleanup signal after the worker has committed
success. SQL correctly prevents a later cancellation from overwriting a winning
terminal state. Shortening the lease or adding another listener does not establish
prompt cancellation while the gateway continues reading/renewing it.

Keep the original HTTP/NDJSON contract, strict tests and `qualified: false`.
Closing the gate requires supported provider lifecycle behavior or an explicitly
reviewed transport/deployment/client contract change. The documented
[Vercel cancellation opt-in](https://vercel.com/docs/functions/functions-api-reference#cancel-requests)
is Node.js-only; it is not a verified Python fix. Persistent FastAPI's local
socket cancellation remains separate evidence. No Node ingress, client
acknowledgement protocol or mandatory new hosting dependency was introduced.

The user explicitly chose to preserve the current contract and experimental
status and continue independent qualification/installer work. This records the
development direction, not a waiver of the strict cancellation gate or a claim
that the hosted limitation is fixed.

Diagnostic sources are in
[`supabase/experiments/disconnect-probe/`](../../../../supabase/experiments/disconnect-probe/README.md).
They are excluded from release assets and must be used only on an isolated
installation. The strict inference probe remains
[`scripts.supabase_hosted_check`](../../../../scripts/supabase_hosted_check.py).
