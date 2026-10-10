import { createGateway } from "./gateway.ts";
import { Rpc } from "./rpc.ts";
import { ResultStorage } from "./storage.ts";

const url = Deno.env.get("OSHEPHERD_SUPABASE_URL") ??
  Deno.env.get("SUPABASE_URL");
const key = Deno.env.get("OSHEPHERD_GATEWAY_KEY") ??
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
const cluster = Deno.env.get("OSHEPHERD_CLUSTER_ID");
const schema = Deno.env.get("OSHEPHERD_SCHEMA") ?? "oshepherd_api";
if (!url || !key || !cluster) throw new Error("Missing gateway configuration");
const handler = createGateway({
  rpc: new Rpc(url.replace(/\/$/, ""), key, schema),
  clusterId: cluster,
  storage: new ResultStorage(url.replace(/\/$/, ""), key, schema),
  publicUrl: Deno.env.get("OSHEPHERD_PUBLIC_URL") ??
    Deno.env.get("SUPABASE_URL"),
  allowedOrigins: (Deno.env.get("OSHEPHERD_ALLOWED_ORIGINS") ?? "").split(",")
    .filter(Boolean),
});
Deno.serve((request, info) => {
  // Newer Deno hosts expose completion rejection separately from Request.signal.
  // Use both hooks when available; the proxy disconnect gate remains measured
  // through the real runtime, rather than inferred from this callback alone.
  const completed =
    (info as Deno.ServeHandlerInfo & { completed?: Promise<void> }).completed;
  if (!completed) return handler(request);
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (request.signal.aborted) abort();
  request.signal.addEventListener("abort", abort, { once: true });
  completed.catch(abort).finally(() =>
    request.signal.removeEventListener("abort", abort)
  );
  return handler(new Request(request, { signal: controller.signal }));
});
