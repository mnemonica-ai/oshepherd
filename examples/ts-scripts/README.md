# Oshepherd in JavaScript/TypeScript

This example demonstrates streaming through the legacy Redis/FastAPI setup.
It currently connects to `http://127.0.0.1:5001`, requests `mistral`, and does not
send an application key. It is not a Supabase onboarding example; use the
[client walkthrough](../../docs/supabase-client-walkthrough.md) for that flow.

## Requirements

Before running this example, follow the [Redis setup guide](../../docs/setup-redis.md)
and ensure a worker serves `mistral`. Change the host/model in
`pretty_streaming.ts` if your setup differs.

For this TypeScript example, you'll also need:

- Node.js (v16 or higher)
- Yarn package manager

## Usage

```bash
yarn install
yarn dev
```

This script will send requests to the Oshepherd API, which will orchestrate Ollama models inference execution on available Oshepherd Workers using streaming mode.
