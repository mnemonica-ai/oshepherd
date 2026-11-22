/** Privileged object access occurs only after the gateway's SQL caller check. */
import { GatewayError, JsonObject, object } from "./normalize.ts";
import { bodyBytes, decoder, sha256 } from "./rpc.ts";

const pathPattern =
  /^(?:[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\/){3}result\.json$/;

export class ResultStorage {
  private bucket: string;
  constructor(
    private url: string,
    private key: string,
    schema: string,
    private fetcher: typeof fetch = fetch,
  ) {
    this.bucket = schema === "oshepherd_spike_api"
      ? "oshepherd-spike-results"
      : "oshepherd-results";
  }
  private headers(): Record<string, string> {
    const headers: Record<string, string> = { apikey: this.key };
    if (this.key.startsWith("eyJ")) {
      headers.Authorization = "Bearer " + this.key;
    }
    return headers;
  }
  private path(path: string): string {
    if (!pathPattern.test(path)) throw new GatewayError("invalid_result");
    return this.bucket + "/" + path;
  }
  async read(
    descriptor: JsonObject,
    signal?: AbortSignal,
    limit = 1048576,
  ): Promise<Uint8Array> {
    if (descriptor.size_bytes > limit) {
      throw new GatewayError("result_too_large", 413);
    }
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, 10000);
    try {
      const response = await this.fetcher(
        this.url + "/storage/v1/object/" + this.path(descriptor.object_path),
        { headers: this.headers(), signal: controller.signal },
      );
      if (!response.ok) {
        await response.body?.cancel();
        throw new GatewayError("storage_unavailable");
      }
      const content = await bodyBytes(response.body, limit);
      if (
        content.length !== descriptor.size_bytes ||
        await sha256(content) !== descriptor.checksum
      ) throw new GatewayError("invalid_result");
      return content;
    } catch (error) {
      if (error instanceof GatewayError) throw error;
      throw new GatewayError("storage_unavailable");
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    }
  }
  async sign(
    descriptor: JsonObject,
    expiresAt: string,
    publicOrigin: string,
  ): Promise<JsonObject> {
    const seconds = Math.min(
      300,
      Math.floor((Date.parse(expiresAt) - Date.now()) / 1000),
    );
    if (seconds < 1) throw new GatewayError("result_expired", 410);
    const response = await this.fetcher(
      this.url + "/storage/v1/object/sign/" + this.path(descriptor.object_path),
      {
        method: "POST",
        headers: { ...this.headers(), "Content-Type": "application/json" },
        body: JSON.stringify({ expiresIn: seconds }),
        signal: AbortSignal.timeout(10000),
      },
    );
    if (!response.ok) {
      await response.body?.cancel();
      throw new GatewayError("storage_unavailable");
    }
    let value: any;
    try {
      value = JSON.parse(decoder.decode(await bodyBytes(response.body, 65536)));
    } catch {
      throw new GatewayError("invalid_storage_response");
    }
    if (
      !object(value) || typeof value.signedURL !== "string" ||
      !value.signedURL.startsWith(
        "/object/sign/" + this.path(descriptor.object_path) + "?",
      )
    ) throw new GatewayError("invalid_storage_response");
    const origin = new URL(publicOrigin).origin;
    return {
      url: origin + "/storage/v1" + value.signedURL,
      size_bytes: descriptor.size_bytes,
      sha256: descriptor.checksum,
      content_type: descriptor.content_type,
      expires_at: new Date(Date.now() + seconds * 1000).toISOString(),
    };
  }
}
