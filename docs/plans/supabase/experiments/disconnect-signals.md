# Hosted disconnect signal investigation

> Dated evidence. Results and feature gaps below describe the recorded revision.
> See the [progress ledger](../progress.md) for current status and the
> [documentation index](../../../README.md) for current user instructions.

Recorded October 11, 2026 on the authorized isolated Supabase Free target.
This investigation does not close the strict cancellation gate or qualify the Edge
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


## Consequence and remaining decision

The measured Edge gateway receives its cleanup signal after the worker has committed
success. SQL correctly prevents a later cancellation from overwriting a winning
terminal state. Shortening the lease or adding another listener does not establish
prompt cancellation while the gateway continues reading/renewing it.

Keep the original HTTP/NDJSON contract, strict tests and `qualified: false`.
Closing the gate requires supported provider lifecycle behavior or an explicitly
reviewed transport/deployment/client contract change. Persistent FastAPI's local socket cancellation remains separate evidence. No Node ingress, client
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
