import { createGateway } from "./gateway.ts";
import { GatewayError, normalize } from "./normalize.ts";
import { encoder, Rpc, sha256 } from "./rpc.ts";

const fixtures = JSON.parse(
  await Deno.readTextFile(
    new URL("../../../protocol/v1/fixtures/routes.json", import.meta.url),
  ),
);
const worker = JSON.parse(
  await Deno.readTextFile(
    new URL("../../../protocol/v1/fixtures/worker.json", import.meta.url),
  ),
);
const cluster = "00000000-0000-4000-8000-000000000001";
const jobId = "00000000-0000-4000-8000-000000000002";
const key = "osk_" + "a".repeat(43);

function assert(value: unknown, label = "assertion failed"): asserts value {
  if (!value) throw new Error(label);
}
function equal(actual: unknown, expected: unknown, label: string) {
  const canonical = (value: any): string =>
    JSON.stringify(
      value,
      (_, item) =>
        item && typeof item === "object" && !Array.isArray(item)
          ? Object.fromEntries(
            Object.entries(item).sort(([a], [b]) => a.localeCompare(b)),
          )
          : item,
    );
  assert(canonical(actual) === canonical(expected), label);
}

for (const fixture of fixtures.cases.filter((value: any) => value.normalized)) {
  Deno.test("payload fixture: " + fixture.id, () => {
    equal(
      normalize(fixture.normalized.type, fixture.request.json),
      fixture.normalized,
      "normalization mismatch",
    );
  });
}

Deno.test("RPC lost acknowledgement preserves operation and separates credentials", async () => {
  const bodies: string[] = [];
  const rpc = new Rpc(
    "http://backend",
    "sb_secret_test",
    "oshepherd_spike_api",
    async (_url, init) => {
      bodies.push(init!.body as string);
      assert(
        (init!.headers as Record<string, string>).apikey === "sb_secret_test",
      );
      assert(!(init!.headers as Record<string, string>).Authorization);
      if (bodies.length === 1) throw new TypeError("lost acknowledgement");
      return new Response(JSON.stringify({ v: 1, data: { ok: true } }));
    },
  );
  equal(await rpc.call("backend_info", {}), { ok: true }, "RPC response");
  assert(
    bodies.length === 2 && bodies[0] === bodies[1],
    "nonce changed after lost acknowledgement",
  );
});

function mockGateway(handler: (name: string, request: any) => any, extra = {}) {
  const rpc = new Rpc(
    "http://backend",
    "sb_secret_test",
    "oshepherd_spike_api",
    async (url, init) => {
      const request = JSON.parse(init!.body as string).p_request;
      const name = String(url).split("/").pop()!;
      const data = await handler(name, request);
      return new Response(JSON.stringify({ v: 1, data }));
    },
  );
  return createGateway({ rpc, clusterId: cluster, pollMs: 1, ...extra });
}
function request(path: string, payload?: unknown, options: RequestInit = {}) {
  return new Request("http://gateway/functions/v1/ollama" + path, {
    method: payload === undefined ? "GET" : "POST",
    headers: {
      Authorization: "Bearer " + key,
      "Content-Type": "application/json",
    },
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
    ...options,
  });
}
function statusJob(overrides = {}) {
  return {
    job_id: jobId,
    state: "succeeded",
    mode: "async",
    attempts: 1,
    last_seq: 0,
    output_bytes: 2,
    created_at: "2026-10-04T12:00:00.000Z",
    updated_at: "2026-10-04T12:00:01.000Z",
    execution_deadline: "2026-10-04T12:01:00.000Z",
    content_expires_at: "2026-10-05T12:00:01.000Z",
    error: null,
    content_available: true,
    chunks: [],
    cursor: 0,
    ...overrides,
  };
}

for (const operation of ["generate", "chat", "embeddings", "systemone"]) {
  Deno.test("HTTP complete: " + operation, async () => {
    const fixture = fixtures.cases.find((value: any) =>
      value.normalized?.type === operation
    );
    const content = JSON.stringify(worker[operation]);
    const descriptor = {
      inline: content,
      object_path: null,
      size_bytes: encoder.encode(content).length,
      checksum: await sha256(encoder.encode(content)),
      content_type: "application/json",
    };
    const gateway = mockGateway((name, payload) => {
      if (name === "backend_info") return {};
      if (name === "submit_job") {
        equal(payload.request, fixture.normalized, "submitted payload");
        return { job_id: jobId };
      }
      if (name === "read_updates") {
        return { jobs: [statusJob({ mode: "sync", result: descriptor })] };
      }
      throw new Error("Unexpected RPC");
    });
    const response = await gateway(
      request(fixture.request.path, fixture.request.json),
    );
    assert(response.status === 200);
    equal(await response.json(), worker[operation], "inference response");
  });
}

for (const operation of ["generate", "chat"]) {
  Deno.test("HTTP exact NDJSON: " + operation, async () => {
    const content = await Deno.readTextFile(
      new URL(
        `../../../protocol/v1/fixtures/${operation}.legacy.ndjson`,
        import.meta.url,
      ),
    );
    const parts = content.split("\n").filter(Boolean).map((line) =>
      line + "\n"
    );
    const chunks = await Promise.all(
      parts.map(async (part, index) => ({
        seq: index + 1,
        content: part,
        checksum: await sha256(encoder.encode(part)),
      })),
    );
    const gateway = mockGateway((name, input) => {
      if (name === "backend_info") return {};
      if (name === "submit_job") return { job_id: jobId };
      if (name === "read_updates") {
        return {
          jobs: [
            statusJob({
              mode: "sync",
              last_seq: chunks.length,
              chunks: chunks.filter((chunk) =>
                chunk.seq > input.cursors[0].cursor
              ),
            }),
          ],
        };
      }
      throw new Error("Unexpected RPC");
    });
    const payload = { model: "fixture", prompt: "hello", stream: true };
    const response = await gateway(request("/api/" + operation, payload));
    assert(
      response.status === 200 &&
        response.headers.get("Content-Type") === "application/x-ndjson",
    );
    assert(await response.text() === content, "NDJSON bytes changed");
  });
}

for (const operation of ["tags", "ps", "show", "version"]) {
  Deno.test("HTTP metadata: " + operation, async () => {
    const expected = operation === "show"
      ? { model_info: worker.show }
      : worker[operation];
    const gateway = mockGateway((name) =>
      name === "backend_info" ? {} : { response: expected }
    );
    const response = await gateway(
      request(
        "/api/" + operation,
        operation === "show" ? { model: "fixture" } : undefined,
      ),
    );
    assert(response.status === 200);
    equal(await response.json(), expected, "metadata response");
  });
}

Deno.test("all routes authenticate before parsing bodies", async () => {
  let calls = 0;
  const gateway = mockGateway(() => {
    calls++;
    return {};
  });
  for (
    const path of [
      "/health",
      "/api/generate",
      "/api/chat",
      "/api/embeddings",
      "/v1/systemone",
      "/api/tags",
      "/api/ps",
      "/api/show",
      "/api/version",
      "/jobs",
    ]
  ) {
    const response = await gateway(
      request(path, undefined, {
        headers: {},
        method: "POST",
        body: "{broken",
      }),
    );
    assert(response.status === 401);
  }
  assert(calls === 0);
  const health = await gateway(request("/health"));
  equal(await health.json(), { status: 200 }, "health response");
});

Deno.test("async acknowledgement, status and exact retained result", async () => {
  const content = '{"done": true}';
  const result = {
    inline: content,
    object_path: null,
    size_bytes: content.length,
    checksum: await sha256(encoder.encode(content)),
    content_type: "application/json",
  };
  const gateway = mockGateway((name) =>
    name === "backend_info"
      ? {}
      : name === "submit_job"
      ? { job_id: jobId, state: "queued" }
      : { jobs: [statusJob({ result, output_bytes: content.length })] }
  );
  const response = await gateway(
    request("/jobs", {
      endpoint: "/api/generate",
      payload: { model: "fixture", prompt: "hello" },
    }),
  );
  assert(
    response.status === 202 &&
      response.headers.get("Location")?.endsWith(jobId),
  );
  assert((await response.json()).id === jobId);
  const state = await (await gateway(request("/jobs/" + jobId))).json();
  assert(
    state.id === jobId && state.status === "succeeded" &&
      state.result.available,
  );
  assert(
    await (await gateway(request("/jobs/" + jobId + "/result"))).text() ===
      content,
  );
});

Deno.test("stream disconnect cancels job and cleans reader", async () => {
  let cancelled = false;
  const content = '{"done":false}\n';
  const checksum = await sha256(encoder.encode(content));
  const gateway = mockGateway((name) => {
    if (name === "backend_info") return {};
    if (name === "submit_job") return { job_id: jobId };
    if (name === "cancel_job") {
      cancelled = true;
      return {};
    }
    return {
      jobs: [
        statusJob({
          state: "running",
          mode: "sync",
          last_seq: 1,
          chunks: [{ seq: 1, content, checksum }],
        }),
      ],
    };
  });
  const response = await gateway(
    request("/api/generate", {
      model: "fixture",
      prompt: "hello",
      stream: true,
    }),
  );
  const reader = response.body!.getReader();
  await reader.read();
  await reader.cancel();
  assert(cancelled, "disconnect did not cancel SQL job");
});

Deno.test("unconsumed stream stalls without polling or unbounded buffering", async () => {
  let reads = 0;
  let reason: string | undefined;
  let notify!: () => void;
  const cancelled = new Promise<void>((resolve) => notify = resolve);
  const gateway = mockGateway((name, payload) => {
    if (name === "backend_info") return {};
    if (name === "submit_job") return { job_id: jobId };
    if (name === "cancel_job") {
      reason = payload.reason;
      notify();
      return {};
    }
    reads++;
    return { jobs: [statusJob({ state: "running", mode: "sync" })] };
  }, { readerStallMs: 10, deadlineMs: 500 });
  const response = await gateway(request("/api/generate", {
    model: "fixture", prompt: "", stream: true,
  }));
  let timer: ReturnType<typeof setTimeout>;
  try {
    await Promise.race([cancelled, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("stall timeout did not cancel")), 250);
    })]);
    assert(reason === "reader_stalled" && reads === 0);
    const reader = response.body!.getReader();
    const first = await reader.read();
    assert(new TextDecoder().decode(first.value).includes("reader_stalled"));
    assert((await reader.read()).done);
  } finally {
    clearTimeout(timer!);
    await response.body?.cancel().catch(() => {});
  }
});

Deno.test("active reader waiting for inference is not a stalled reader", async () => {
  let ready = false;
  const cancellations: string[] = [];
  const content = '{"done":true}\n';
  const checksum = await sha256(encoder.encode(content));
  const gateway = mockGateway((name, payload) => {
    if (name === "backend_info") return {};
    if (name === "submit_job") return { job_id: jobId };
    if (name === "cancel_job") {
      cancellations.push(payload.reason);
      return {};
    }
    return { jobs: [statusJob({
      state: ready ? "succeeded" : "running", mode: "sync", last_seq: ready ? 1 : 0,
      chunks: ready && payload.cursors[0].cursor === 0 ? [{ seq: 1, content, checksum }] : [],
    })] };
  }, { readerStallMs: 10, deadlineMs: 1000 });
  const response = await gateway(request("/api/generate", {
    model: "fixture", prompt: "", stream: true,
  }));
  const reader = response.body!.getReader();
  try {
    const pending = reader.read();
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert(cancellations.length === 0);
    ready = true;
    assert(new TextDecoder().decode((await pending).value) === content);
    assert((await reader.read()).done);
    assert(cancellations.length === 0);
  } finally {
    await reader.cancel();
  }
});

Deno.test("new gateway rejects malformed and unsupported payloads before admission", async () => {
  let admitted = false;
  const gateway = mockGateway((name) => {
    if (name === "submit_job") admitted = true;
    return {};
  });
  for (
    const [path, payload] of [["/api/generate", {}], ["/api/chat", {
      model: "test",
      messages: [{ role: "tool", content: "x" }],
    }], ["/jobs", {
      endpoint: "/api/generate",
      payload: { model: "test", prompt: "x", stream: true },
    }]]
  ) {
    assert((await gateway(request(path as string, payload))).status === 422);
  }
  assert(!admitted);
});
