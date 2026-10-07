import { GatewayError, JsonObject, object } from "./normalize.ts";

export const encoder = new TextEncoder();
export const decoder = new TextDecoder("utf-8", { fatal: true });

export async function bodyBytes(
  body: ReadableStream<Uint8Array> | null,
  limit: number,
): Promise<Uint8Array> {
  if (!body) return new Uint8Array();
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > limit) throw new GatewayError("request_too_large", 413);
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}

export function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new GatewayError("cancelled", 499));
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(new GatewayError("cancelled", 499));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

function retryAfter(value: string | null): number | null {
  if (value === null) return null;
  const number = Number(value);
  const seconds = Number.isFinite(number)
    ? number
    : (Date.parse(value) - Date.now()) / 1000;
  return Number.isFinite(seconds)
    ? Math.min(2, Math.max(0, seconds)) * 1000
    : null;
}

export class Rpc {
  constructor(
    public url: string,
    private key: string,
    public schema: string,
    private fetcher: typeof fetch = fetch,
  ) {
    if (!["oshepherd_api", "oshepherd_spike_api"].includes(schema)) {
      throw new GatewayError("unsupported_protocol", 409);
    }
  }
  async call(
    name: string,
    request: JsonObject,
    signal?: AbortSignal,
  ): Promise<JsonObject> {
    const body = JSON.stringify({
      p_request: { v: 1, operation_id: crypto.randomUUID(), ...request },
    });
    for (let attempt = 0; attempt < 4; attempt++) {
      if (signal?.aborted) throw new GatewayError("cancelled", 499);
      const controller = new AbortController();
      const abort = () => controller.abort();
      signal?.addEventListener("abort", abort, { once: true });
      const timer = setTimeout(abort, 10000);
      let delay: number | null = null;
      let retry = false;
      try {
        const headers: Record<string, string> = {
          apikey: this.key,
          "Content-Profile": this.schema,
          "Accept-Profile": this.schema,
          "Content-Type": "application/json",
        };
        if (this.key.startsWith("eyJ")) {
          headers.Authorization = "Bearer " + this.key;
        }
        const response = await this.fetcher(this.url + "/rest/v1/rpc/" + name, {
          method: "POST",
          headers,
          body,
          signal: controller.signal,
        });
        delay = retryAfter(response.headers.get("Retry-After"));
        const bytes = await bodyBytes(response.body, 2097152);
        if ([429, 502, 503, 504].includes(response.status) && attempt < 3) {
          retry = true;
          continue;
        }
        let value: any;
        try {
          value = JSON.parse(decoder.decode(bytes));
        } catch {
          throw new GatewayError("invalid_rpc_response");
        }
        if (!response.ok) {
          const code = object(value) && typeof value.message === "string" &&
              /^[a-z][a-z0-9_]{0,63}$/.test(value.message)
            ? value.message
            : "backend_error";
          throw new GatewayError(code, response.status);
        }
        if (!object(value) || value.v !== 1 || !object(value.data)) {
          throw new GatewayError("protocol_mismatch");
        }
        return value.data;
      } catch (error) {
        if (error instanceof GatewayError) throw error;
        if (signal?.aborted) throw new GatewayError("cancelled", 499);
        if (attempt === 3) throw new GatewayError("backend_unavailable");
        retry = true;
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
        if (retry) {
          await pause(
            delay ??
              Math.min(200 * 2 ** attempt, 2000) * (0.8 + Math.random() * 0.4),
            signal,
          );
        }
      }
    }
    throw new GatewayError("backend_unavailable");
  }
}

export async function sha256(bytes: Uint8Array): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(
    (x) => x.toString(16).padStart(2, "0"),
  ).join("");
}
