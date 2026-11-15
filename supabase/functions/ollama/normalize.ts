/** Legacy payload defaults and field coercions, without backend resources. */
export type JsonObject = Record<string, any>;

export class GatewayError extends Error {
  constructor(public code: string, public status = 503) {
    super(code);
  }
}

export function object(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function string(value: unknown): string {
  if (typeof value !== "string") throw new GatewayError("invalid_request", 422);
  return value;
}

function boolean(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (value === 0 || value === 1) return !!value;
  if (typeof value === "string") {
    const lower = value.toLowerCase();
    if (["0", "off", "f", "false", "n", "no"].includes(lower)) return false;
    if (["1", "on", "t", "true", "y", "yes"].includes(lower)) return true;
  }
  throw new GatewayError("invalid_request", 422);
}

function integer(value: unknown): number {
  if (typeof value === "boolean") return Number(value);
  if (typeof value === "string" && /^\s*[+-]?\d+(?:\.0+)?\s*$/.test(value)) {
    value = Number(value);
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new GatewayError("invalid_request", 422);
  }
  return value;
}

const validators: Record<string, (value: any) => any> = {
  model: string,
  prompt: string,
  input: string,
  format: string,
  system: string,
  template: string,
  keep_alive: string,
  stream: boolean,
  raw: boolean,
  verbose: boolean,
  options: (value) => {
    if (!object(value)) throw new GatewayError("invalid_request", 422);
    return value;
  },
  images: (value) => {
    if (!Array.isArray(value)) throw new GatewayError("invalid_request", 422);
    return value.map(string);
  },
  context: (value) => {
    if (!Array.isArray(value)) throw new GatewayError("invalid_request", 422);
    return value.map(integer);
  },
  messages: (value) => {
    if (!Array.isArray(value)) throw new GatewayError("invalid_request", 422);
    return value.map((message) => {
      if (
        !object(message) ||
        !["user", "assistant", "system"].includes(message.role)
      ) throw new GatewayError("invalid_request", 422);
      return { role: message.role, content: string(message.content) };
    });
  },
};

const defaults: Record<string, JsonObject> = {
  generate: {
    images: null,
    format: "json",
    options: null,
    system: null,
    template: null,
    context: null,
    stream: false,
    raw: false,
    keep_alive: "5m",
  },
  chat: {
    messages: null,
    format: "",
    options: {},
    stream: false,
    keep_alive: null,
  },
  embeddings: { options: null, keep_alive: "5m" },
  show: { verbose: false },
};
const required: Record<string, string[]> = {
  generate: ["model", "prompt"],
  chat: ["model"],
  embeddings: ["model", "input"],
  systemone: ["model", "state", "questions"],
  show: ["model"],
};

export function normalize(
  operation: string,
  raw: unknown,
  asynchronous = false,
): JsonObject {
  if (!object(raw) || !required[operation]) {
    throw new GatewayError("invalid_request", 422);
  }
  // The Python SDK's legacy embeddings method sends prompt; retain input too.
  const input = operation === "embeddings" && !("input" in raw) && "prompt" in raw
    ? { ...raw, input: raw.prompt }
    : raw;
  const payload: JsonObject = {};
  for (const field of required[operation]) {
    if (!(field in input)) throw new GatewayError("invalid_request", 422);
    if (field === "state") {
      if (
        typeof input.state !== "string" && !object(input.state) &&
        !Array.isArray(input.state)
      ) throw new GatewayError("invalid_request", 422);
      payload.state = input.state;
    } else if (field === "questions") {
      if (!object(input.questions)) {
        throw new GatewayError("invalid_request", 422);
      }
      payload.questions = input.questions;
    } else payload[field] = string(input[field]);
  }
  if (operation === "systemone") {
    return { type: operation, payload: { ...input, ...payload } };
  }
  for (const [field, fallback] of Object.entries(defaults[operation])) {
    payload[field] = !(field in input)
      ? structuredClone(fallback)
      : input[field] === null
      ? null
      : validators[field](input[field]);
  }
  if (asynchronous && payload.stream === true) {
    throw new GatewayError("async_streaming_unsupported", 422);
  }
  return { type: operation, payload };
}
