import { GatewayError, JsonObject, normalize, object } from "./normalize.ts";
import { bodyBytes, decoder, encoder, pause, Rpc, sha256 } from "./rpc.ts";
import { ResultStorage } from "./storage.ts";

const paths: Record<string, string> = {
  "/api/generate": "generate",
  "/api/chat": "chat",
  "/api/embeddings": "embeddings",
  "/v1/systemone": "systemone",
};
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const terminal = new Set(["succeeded", "failed", "cancelled", "expired"]);

export interface GatewayConfig {
  rpc: Rpc;
  clusterId: string;
  prefix?: string;
  allowedOrigins?: string[];
  pollMs?: number;
  deadlineMs?: number;
  readerStallMs?: number;
  storage?: ResultStorage;
  publicUrl?: string;
}

function json(value: unknown, status = 200, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function failure(job: JsonObject): GatewayError {
  return new GatewayError(
    job.error?.code ??
      (job.state === "expired" ? "execution_expired" : "upstream_error"),
    job.state === "expired" ? 504 : 500,
  );
}

function status(job: JsonObject): JsonObject {
  return {
    id: job.job_id,
    status: job.state,
    attempt: job.attempts,
    created_at: job.created_at,
    updated_at: job.updated_at,
    execution_deadline: job.execution_deadline,
    error: job.error,
    result: {
      available: job.state === "succeeded" && job.content_available,
      size_bytes: job.state === "succeeded" ? job.output_bytes : null,
      checksum: job.result?.checksum ?? null,
      expires_at: job.content_expires_at,
    },
  };
}

export function createGateway(
  config: GatewayConfig,
): (request: Request) => Promise<Response> {
  if (!uuid.test(config.clusterId)) {
    throw new GatewayError("invalid_configuration", 500);
  }
  const rpc = config.rpc;
  const poll = config.pollMs ?? 250;
  const budget = Math.min(config.deadlineMs ?? 135000, 135000);
  const stall = config.readerStallMs ?? 30000;
  return async (request) => {
    const cors: Record<string, string> = {};
    const origin = request.headers.get("Origin");
    if (origin && config.allowedOrigins?.includes(origin)) {
      cors["Access-Control-Allow-Origin"] = origin;
      cors.Vary = "Origin";
    }
    if (request.method === "OPTIONS") {
      if (!origin || !cors["Access-Control-Allow-Origin"]) {
        return json({ error: "forbidden" }, 403);
      }
      return new Response(null, {
        status: 204,
        headers: {
          ...cors,
          "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
          "Access-Control-Allow-Headers":
            "Authorization,Content-Type,Idempotency-Key",
          "Access-Control-Max-Age": "600",
        },
      });
    }
    const withCors = (response: Response): Response => {
      for (const [name, value] of Object.entries(cors)) {
        response.headers.set(name, value);
      }
      if (!response.headers.has("Cache-Control")) {
        response.headers.set("Cache-Control", "private, no-store");
      }
      return response;
    };
    try {
      let path = new URL(request.url).pathname;
      for (
        const prefix of [config.prefix ?? "/functions/v1/ollama", "/ollama"]
      ) {
        if (path === prefix || path.startsWith(prefix + "/")) {
          path = path.slice(prefix.length) || "/";
          break;
        }
      }
      const bearer = /^Bearer (osk_[A-Za-z0-9_-]{43})$/i.exec(
        request.headers.get("Authorization") ?? "",
      );
      if (!bearer) throw new GatewayError("unauthorized", 401);
      const caller = { cluster_id: config.clusterId, key: bearer[1] };
      // Authenticate before parsing payloads, metadata, health or job IDs.
      await rpc.call("backend_info", caller, request.signal);
      const parseBody = async (): Promise<JsonObject> => {
        try {
          const input = JSON.parse(
            decoder.decode(
              await bodyBytes(request.body, 1048576, {
                bytes: 8 * 1048576,
                milliseconds: 5000,
              }),
            ),
          );
          if (!object(input)) throw new GatewayError("invalid_request", 422);
          return input;
        } catch (error) {
          if (error instanceof GatewayError) throw error;
          throw new GatewayError("invalid_request", 422);
        }
      };
      const read = async (
        jobId: string,
        cursor: number,
        token: string | null,
        signal?: AbortSignal,
      ): Promise<JsonObject> => {
        const page = await rpc.call("read_updates", {
          ...caller,
          cursors: [{ job_id: jobId, cursor, consumer_token: token }],
          max_bytes: 262144,
          max_chunks: 64,
        }, signal);
        if (!Array.isArray(page.jobs) || page.jobs.length !== 1) {
          throw new GatewayError("protocol_mismatch");
        }
        return page.jobs[0];
      };
      const cancel = async (
        jobId: string,
        token: string | null,
        reason = "cancelled",
      ) => {
        return await rpc.call("cancel_job", {
          ...caller,
          job_id: jobId,
          consumer_token: token,
          reason,
        });
      };
      if (path === "/health" && request.method === "GET") {
        return withCors(json({ status: 200 }));
      }
      const metadata = /^\/api\/(tags|ps|show|version)$/.exec(path);
      if (metadata) {
        const operation = metadata[1];
        if (request.method !== (operation === "show" ? "POST" : "GET")) {
          throw new GatewayError("method_not_allowed", 405);
        }
        const model = operation === "show"
          ? normalize("show", await parseBody()).payload.model
          : null;
        const value = await rpc.call("read_metadata", {
          ...caller,
          operation,
          model,
        }, request.signal);
        return withCors(json(value.response));
      }
      const jobPath = /^\/jobs\/([^/]+)(\/result)?$/.exec(path);
      if (jobPath) {
        const jobId = jobPath[1];
        if (!uuid.test(jobId)) throw new GatewayError("not_found", 404);
        if (
          request.method !== "GET" &&
          !(request.method === "DELETE" && !jobPath[2])
        ) {
          throw new GatewayError("method_not_allowed", 405);
        }
        const job = await read(jobId, 0, null, request.signal);
        if (job.mode !== "async") throw new GatewayError("not_found", 404);
        if (request.method === "DELETE") {
          return withCors(json(status(await cancel(jobId, null))));
        }
        if (!jobPath[2]) return withCors(json(status(job)));
        if (!terminal.has(job.state)) {
          return withCors(json({ id: jobId, status: job.state }, 202));
        }
        if (!job.content_available) {
          throw new GatewayError("result_expired", 410);
        }
        if (job.state !== "succeeded") {
          return withCors(json({ error: job.error?.code ?? job.state }, 409));
        }
        if (new URL(request.url).searchParams.get("download") === "true") {
          if (!config.storage || !job.result?.object_path) {
            throw new GatewayError("storage_not_configured", 503);
          }
          return withCors(
            json(
              await config.storage.sign(
                job.result,
                job.content_expires_at,
                config.publicUrl ?? request.url,
              ),
            ),
          );
        }
        return withCors(
          await inlineResult(job, config.storage, request.signal),
        );
      }
      const asynchronous = path === "/jobs";
      const operation = paths[path];
      if (!asynchronous && !operation) throw new GatewayError("not_found", 404);
      if (request.method !== "POST") {
        throw new GatewayError("method_not_allowed", 405);
      }
      const body = await parseBody();
      let normalized: JsonObject;
      let limits: JsonObject = {};
      if (asynchronous) {
        if (
          Object.keys(body).some((key) =>
            !["endpoint", "payload", "limits"].includes(key)
          ) || !paths[body.endpoint]
        ) throw new GatewayError("invalid_request", 422);
        normalized = normalize(paths[body.endpoint], body.payload, true);
        limits = body.limits ?? {};
        if (!object(limits)) throw new GatewayError("invalid_request", 422);
      } else {
        normalized = normalize(operation, body);
        limits.total_ms = budget;
      }
      const idempotency = request.headers.get("Idempotency-Key");
      if (
        idempotency !== null &&
        (idempotency.length < 1 || idempotency.length > 128)
      ) throw new GatewayError("invalid_request", 422);
      const token = asynchronous ? null : crypto.randomUUID();
      const controller = new AbortController();
      let jobId: string | undefined;
      let detached = false;
      let cancelled = false;
      let stopReason = "cancelled";
      const stop = async (reason: string) => {
        if (!controller.signal.aborted) stopReason = reason;
        controller.abort();
        if (jobId && !cancelled) {
          cancelled = true;
          await cancel(jobId, token, reason).catch(() => {});
        }
      };
      const abort = () => {
        if (!asynchronous) void stop("cancelled");
      };
      const timeout = setTimeout(() => {
        if (!asynchronous) void stop("execution_expired");
      }, budget);
      request.signal.addEventListener("abort", abort, { once: true });
      const cleanup = () => {
        clearTimeout(timeout);
        request.signal.removeEventListener("abort", abort);
      };
      try {
        // Async admission survives invocation disconnection. Sync submission is
        // also allowed to resolve its nonce before cancelling an accepted job.
        const job = await rpc.call("submit_job", {
          ...caller,
          request: normalized,
          mode: asynchronous ? "async" : "sync",
          limits,
          idempotency_key: idempotency,
          consumer_token: token,
        });
        jobId = job.job_id;
        if (!jobId || !uuid.test(jobId)) {
          throw new GatewayError("protocol_mismatch");
        }
        if (asynchronous) {
          detached = true;
          const url = new URL(request.url);
          if (config.publicUrl) {
            const publicUrl = new URL(config.publicUrl);
            url.protocol = publicUrl.protocol;
            url.host = publicUrl.host;
          }
          url.pathname = config.publicUrl
            ? (config.prefix ?? "/functions/v1/ollama") + "/jobs/" + jobId
            : url.pathname.replace(/\/jobs$/, "/jobs/" + jobId);
          url.search = "";
          return withCors(
            json(
              { id: jobId, status: job.state, status_url: url.toString() },
              202,
              { Location: url.toString() },
            ),
          );
        }
        if (request.signal.aborted || controller.signal.aborted) {
          await stop("cancelled");
          throw new GatewayError("cancelled", 499);
        }
        const stream = ["generate", "chat"].includes(operation) &&
          normalized.payload.stream === true;
        if (!stream) {
          while (true) {
            const page = await read(jobId, 0, token, controller.signal);
            if (page.state === "succeeded") {
              return withCors(
                await inlineResult(page, config.storage, controller.signal),
              );
            }
            if (terminal.has(page.state)) throw failure(page);
            await pause(poll, controller.signal);
          }
        }
        let cursor = 0;
        let page: JsonObject | null = null;
        let chunkIndex = 0;
        let closed = false;
        let stallTimer: ReturnType<typeof setTimeout>;
        const resetStall = () => {
          clearTimeout(stallTimer);
          stallTimer = setTimeout(() => void stop("reader_stalled"), stall);
        };
        const close = () => {
          closed = true;
          clearTimeout(stallTimer);
          cleanup();
        };
        const bytes = new ReadableStream<Uint8Array>({
          start() {
            resetStall();
          },
          async pull(sink) {
            if (closed) return;
            clearTimeout(stallTimer);
            try {
              while (true) {
                if (!page) {
                  page = await read(jobId!, cursor, token, controller.signal);
                  chunkIndex = 0;
                }
                if (chunkIndex < page.chunks.length) {
                  const chunk = page.chunks[chunkIndex++];
                  if (
                    chunk.seq !== cursor + 1 ||
                    typeof chunk.content !== "string"
                  ) throw new GatewayError("sequence_gap");
                  const content = encoder.encode(chunk.content);
                  if (
                    content.length > 65536 ||
                    await sha256(content) !== chunk.checksum
                  ) throw new GatewayError("invalid_chunk");
                  cursor = chunk.seq;
                  sink.enqueue(content);
                  resetStall();
                  return;
                }
                if (terminal.has(page.state) && cursor === page.last_seq) {
                  if (page.state !== "succeeded") {
                    sink.enqueue(
                      encoder.encode(
                        JSON.stringify({
                          error: page.error?.code ?? page.state,
                        }) + "\n",
                      ),
                    );
                  }
                  close();
                  sink.close();
                  return;
                }
                if (cursor < page.last_seq && page.chunks.length === 0) {
                  throw new GatewayError("sequence_gap");
                }
                page = null;
                await pause(poll, controller.signal);
              }
            } catch (error) {
              await stop(
                error instanceof GatewayError
                  ? error.code === "cancelled" ? "cancelled" : "reader_stalled"
                  : "reader_stalled",
              );
              close();
              sink.enqueue(
                encoder.encode(
                  JSON.stringify({
                    error: error instanceof GatewayError
                      ? error.code === "cancelled" ? stopReason : error.code
                      : "backend_unavailable",
                  }) + "\n",
                ),
              );
              sink.close();
            }
          },
          async cancel() {
            close();
            await stop("cancelled");
          },
        }, { highWaterMark: 0 });
        detached = true; // The stream owns timeout/abort cleanup from here.
        return withCors(
          new Response(bytes, {
            headers: {
              "Content-Type": "application/x-ndjson",
              "Cache-Control": "no-store, no-transform",
              "X-Accel-Buffering": "no",
            },
          }),
        );
      } catch (error) {
        if (!asynchronous && jobId) await stop("cancelled");
        if (
          error instanceof GatewayError && error.code === "cancelled" &&
          stopReason === "execution_expired"
        ) throw new GatewayError("execution_expired", 504);
        throw error;
      } finally {
        if (!detached || asynchronous) cleanup();
      }
    } catch (error) {
      const failure = error instanceof GatewayError
        ? error
        : new GatewayError("backend_unavailable");
      const headers = failure.status === 429
        ? { ...cors, "Retry-After": "1" }
        : cors;
      return json({ error: failure.code }, failure.status, headers);
    }
  };
}

async function inlineResult(
  job: JsonObject,
  storage?: ResultStorage,
  signal?: AbortSignal,
): Promise<Response> {
  const descriptor = job.result;
  if (job.result_omitted) throw new GatewayError("result_too_large", 413);
  if (object(descriptor) && descriptor.object_path) {
    if (!storage) throw new GatewayError("storage_not_configured", 503);
    return new Response(await storage.read(descriptor, signal), {
      headers: { "Content-Type": "application/json" },
    });
  }
  if (!object(descriptor) || typeof descriptor.inline !== "string") {
    throw new GatewayError("protocol_mismatch");
  }
  const content = encoder.encode(descriptor.inline);
  if (
    content.length !== descriptor.size_bytes ||
    await sha256(content) !== descriptor.checksum
  ) throw new GatewayError("invalid_result");
  return new Response(content, {
    headers: { "Content-Type": "application/json" },
  });
}
