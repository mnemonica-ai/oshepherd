// Temporary isolated-provider diagnostic, never part of the release gateway.
// Auth uses the existing cluster application key. Reports contain events only;
// the controller reads/deletes their private Storage objects after the probe.
import { Rpc } from "../../functions/ollama/rpc.ts";

const base = Deno.env.get("OSHEPHERD_SUPABASE_URL") ??
  Deno.env.get("SUPABASE_URL");
const secret = Deno.env.get("OSHEPHERD_GATEWAY_KEY") ??
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
const cluster = Deno.env.get("OSHEPHERD_CLUSTER_ID");
if (!base || !secret || !cluster) {
  throw new Error("Missing diagnostic configuration");
}
const rpc = new Rpc(
  base,
  secret,
  Deno.env.get("OSHEPHERD_SCHEMA") ?? "oshepherd_api",
);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

Deno.serve(async (request, info) => {
  const bearer = /^Bearer (osk_[A-Za-z0-9_-]{43})$/.exec(
    request.headers.get("Authorization") ?? "",
  );
  if (!bearer) return new Response(null, { status: 401 });
  try {
    await rpc.call("backend_info", { cluster_id: cluster, key: bearer[1] });
  } catch {
    return new Response(null, { status: 401 });
  }
  const id = request.headers.get("X-Oshepherd-Probe") ?? "";
  if (request.method !== "POST" || !uuid.test(id)) {
    return new Response(null, { status: 422 });
  }
  const started = performance.now();
  const events: { event: string; seconds: number }[] = [];
  const event = (name: string) => {
    if (events.length < 32) {
      events.push({
        event: name,
        seconds: (performance.now() - started) / 1000,
      });
    }
  };
  const completed =
    (info as Deno.ServeHandlerInfo & { completed?: Promise<void> })
      .completed;
  const report = {
    completed_supported: completed !== undefined,
    initial_signal_aborted: request.signal.aborted,
    events,
  };
  request.signal.addEventListener("abort", () => event("request-abort"), {
    once: true,
  });
  completed?.then(
    () => event("completion-resolved"),
    () => event("completion-rejected"),
  );
  // Keep diagnostics alive long enough to distinguish proxy consumption from
  // client closure. This is not an inference job or a cancellation workaround.
  const save = async () => {
    await new Promise((resolve) => setTimeout(resolve, 12000));
    await fetch(
      base + "/storage/v1/object/oshepherd-results/" +
        cluster + "/" + id + "/" + id + "/result.json",
      {
        method: "POST",
        headers: { apikey: secret, "Content-Type": "application/json" },
        body: JSON.stringify(report),
        signal: AbortSignal.timeout(5000),
      },
    ).then(async (response) => {
      await response.body?.cancel();
      if (!response.ok) throw new Error("diagnostic_report_write_failed");
    });
  };
  // Supabase-specific and deliberately confined to this experimental probe.
  (globalThis as unknown as {
    EdgeRuntime: { waitUntil(p: Promise<void>): void };
  })
    .EdgeRuntime.waitUntil(save());
  let sequence = 0;
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (sequence > 0) {
        await new Promise((resolve) => setTimeout(resolve, 4000));
      }
      if (cancelled) return;
      event("pull-" + sequence);
      controller.enqueue(
        new TextEncoder().encode(JSON.stringify({ sequence }) + "\n"),
      );
      if (++sequence === 3) {
        event("stream-close");
        controller.close();
      }
    },
    cancel() {
      cancelled = true;
      event("stream-cancel");
    },
  }, { highWaterMark: 0 });
  return new Response(body, {
    headers: {
      "Content-Type": "application/x-ndjson",
      "Cache-Control": "no-store",
    },
  });
});
