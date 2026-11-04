# Set up oshepherd with Redis

This is oshepherd's original self-hosted setup. One API server receives Ollama
requests and queues them in Redis. Workers, one next to each Ollama server, take
requests from the queue and run them on their local models. Clients talk to the
API server exactly as they would talk to Ollama.

```text
Ollama client ──> oshepherd API ──> Redis ──> oshepherd worker ──> Ollama
                                        └───> oshepherd worker ──> Ollama
```

You run and secure all three parts yourself. There is no cloud account involved.

## What you need

- oshepherd installed on the API machine and on each worker machine. Any version
  works; see [Install](install.md).
- A Redis server that the API and every worker can reach. It is both the Celery
  message broker and its result backend, and it carries streamed responses.
- [Ollama](https://ollama.com/download) running on each worker machine, with the
  models you want to serve already pulled, for example `ollama pull mistral`.

## 1. Start Redis

For a test on one machine, run Redis in Docker:

```sh
docker run -d --name oshepherd-redis -p 6379:6379 redis:7
```

Its URL is `redis://localhost:6379/0`. For machines on a network, use a Redis
server they can all reach, with a password. Use `rediss://` URLs for TLS, for
example with a managed provider such as [Redis Cloud](https://redis.io/cloud/).

## 2. Start the API server

Create a file named `.api.env`:

```env
CELERY_BROKER_URL="redis://localhost:6379/0"
CELERY_BACKEND_URL="redis://localhost:6379/0"
HOST="0.0.0.0"
PORT=5001
LOGLEVEL="info"
```

Start the server and leave it running:

```sh
oshepherd start-api --env-file .api.env
```

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `CELERY_BROKER_URL` | yes | | Redis URL for the request queue |
| `CELERY_BACKEND_URL` | yes | | Redis URL for results and streaming |
| `HOST` | no | `0.0.0.0` | Address the API listens on |
| `PORT` | no | `5001` | Port the API listens on |
| `WORKERS` | no | CPU count minus one | Uvicorn worker processes |
| `LOGLEVEL` | no | `info` | `debug`, `info`, `warning`, `error` or `critical` |
| `UVICORN_ACCESS_LOG` | no | `false` | Log each HTTP request |

## 3. Start a worker

On a machine running Ollama, create a file named `.worker.env`. Use the same Redis
URLs as the API:

```env
CELERY_BROKER_URL="redis://localhost:6379/0"
CELERY_BACKEND_URL="redis://localhost:6379/0"
OLLAMA_BASE_URL="http://localhost:11434"
LOGLEVEL="info"
```

Start the worker and leave it running:

```sh
oshepherd start-worker --env-file .worker.env
```

To add capacity, start more workers on other machines with the same Redis URLs.
`CONCURRENCY` (default `1`) sets how many requests one worker runs at once.

**If Ollama is not at `http://localhost:11434`**, also export `OLLAMA_HOST` before
starting the worker, for example
`OLLAMA_HOST=http://127.0.0.1:11500 oshepherd start-worker --env-file .worker.env`.
`OLLAMA_BASE_URL` only covers the worker's own version check; inference uses the
Ollama library's default address, which reads `OLLAMA_HOST`.

## 4. Send a request

Check that the API is up, then ask for a completion:

```sh
curl http://127.0.0.1:5001/health

curl -X POST -H "Content-Type: application/json" -L http://127.0.0.1:5001/api/generate/ \
  -d '{"model":"mistral","prompt":"Why is the sky blue?","stream":true}' --no-buffer
```

Point any Ollama client at `http://<api-host>:5001`. The
[README](../README.md#call-oshepherd) has Python, JavaScript and System One examples.

## Security

The Redis setup has **no authentication**: anyone who can reach the API port can
run inference on your workers. Keep the API on a private network, or put it
behind a reverse proxy that authenticates requests. Protect Redis with a password
and TLS, because anyone who can reach it can read and inject requests.

## Logging

Set `LOGLEVEL` in either env file. Request and response bodies are only logged at
`debug`, because they can contain prompts, model output and other sensitive data.
Uvicorn access logs are off unless you set `UVICORN_ACCESS_LOG=true`.

## Stop and clean up

Press Ctrl-C in the API and worker terminals. Remove the test Redis container with
`docker rm -f oshepherd-redis`.
