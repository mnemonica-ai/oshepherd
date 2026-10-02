# oshepherd

> _The Oshepherd guiding the Ollama(s) inference orchestration._

<p align="center">
  <img src="https://raw.githubusercontent.com/mnemonica-ai/oshepherd/main/assets/oshepherd_logo.png" alt="oshepherd logo" width="200">
</p>

<p align="center">
  <a href="https://pypi.org/project/oshepherd/"><img src="https://img.shields.io/pypi/v/oshepherd" alt="PyPI Version"></a>
  <a href="https://deepwiki.com/mnemonica-ai/oshepherd"><img src="https://img.shields.io/badge/DeepWiki-mnemonica--ai%2Foshepherd-blue.svg?logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACwAAAAyCAYAAAAnWDnqAAAAAXNSR0IArs4c6QAAA05JREFUaEPtmUtyEzEQhtWTQyQLHNak2AB7ZnyXZMEjXMGeK/AIi+QuHrMnbChYY7MIh8g01fJoopFb0uhhEqqcbWTp06/uv1saEDv4O3n3dV60RfP947Mm9/SQc0ICFQgzfc4CYZoTPAswgSJCCUJUnAAoRHOAUOcATwbmVLWdGoH//PB8mnKqScAhsD0kYP3j/Yt5LPQe2KvcXmGvRHcDnpxfL2zOYJ1mFwrryWTz0advv1Ut4CJgf5uhDuDj5eUcAUoahrdY/56ebRWeraTjMt/00Sh3UDtjgHtQNHwcRGOC98BJEAEymycmYcWwOprTgcB6VZ5JK5TAJ+fXGLBm3FDAmn6oPPjR4rKCAoJCal2eAiQp2x0vxTPB3ALO2CRkwmDy5WohzBDwSEFKRwPbknEggCPB/imwrycgxX2NzoMCHhPkDwqYMr9tRcP5qNrMZHkVnOjRMWwLCcr8ohBVb1OMjxLwGCvjTikrsBOiA6fNyCrm8V1rP93iVPpwaE+gO0SsWmPiXB+jikdf6SizrT5qKasx5j8ABbHpFTx+vFXp9EnYQmLx02h1QTTrl6eDqxLnGjporxl3NL3agEvXdT0WmEost648sQOYAeJS9Q7bfUVoMGnjo4AZdUMQku50McDcMWcBPvr0SzbTAFDfvJqwLzgxwATnCgnp4wDl6Aa+Ax283gghmj+vj7feE2KBBRMW3FzOpLOADl0Isb5587h/U4gGvkt5v60Z1VLG8BhYjbzRwyQZemwAd6cCR5/XFWLYZRIMpX39AR0tjaGGiGzLVyhse5C9RKC6ai42ppWPKiBagOvaYk8lO7DajerabOZP46Lby5wKjw1HCRx7p9sVMOWGzb/vA1hwiWc6jm3MvQDTogQkiqIhJV0nBQBTU+3okKCFDy9WwferkHjtxib7t3xIUQtHxnIwtx4mpg26/HfwVNVDb4oI9RHmx5WGelRVlrtiw43zboCLaxv46AZeB3IlTkwouebTr1y2NjSpHz68WNFjHvupy3q8TFn3Hos2IAk4Ju5dCo8B3wP7VPr/FGaKiG+T+v+TQqIrOqMTL1VdWV1DdmcbO8KXBz6esmYWYKPwDL5b5FA1a0hwapHiom0r/cKaoqr+27/XcrS5UwSMbQAAAABJRU5ErkJggg==" alt="DeepWiki"></a>
  <a href="https://discord.gg/99xEZmZYt9"><img src="https://img.shields.io/discord/1156943457970040843?color=5865F2&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://github.com/mnemonica-ai/oshepherd/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
</p>

A centralized [FastAPI](https://fastapi.tiangolo.com/) service that uses [Celery](https://docs.celeryq.dev) and [Redis](https://redis.com) to orchestrate multiple [Ollama](https://ollama.com) servers as workers.

### Install

```sh
pip install oshepherd
```

### Usage

1. Set up Redis:

    [Celery](https://docs.celeryq.dev) uses [Redis](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/index.html#redis) as a message broker and backend. You'll need a Redis instance, which you can provision for free at [redislabs.com](https://app.redislabs.com).

2. Set up the FastAPI server:

    ```sh
    # define configuration env file
    # use credentials for redis as broker and backend
    cp .api.env.template .api.env

    # start api
    oshepherd start-api --env-file .api.env
    ```

3. Set up the Celery/Ollama workers:

    ```sh
    # install ollama https://ollama.com/download
    # optionally pull the model
    ollama pull mistral

    # define configuration env file
    # use credentials for redis as broker and backend
    cp .worker.env.template .worker.env

    # start worker
    oshepherd start-worker --env-file .worker.env
    ```

#### Logging

Oshepherd uses Python's standard logging library for both the API server and
Celery worker.

Set `LOGLEVEL` in either `.api.env` or `.worker.env` to control verbosity:

```env
LOGLEVEL="info"
```

Valid values include `debug`, `info`, `warning`, `error`, and `critical`.
API access logs from Uvicorn are disabled by default; enable them with:

```env
UVICORN_ACCESS_LOG=true
```

Request and response payload bodies are only logged at `debug` level because
they may contain prompts, model output, or other sensitive data.

4. Now you're ready to execute Ollama completions remotely. Point your Ollama client at your `oshepherd` API server by setting the `host`. The server will return the requested completions from one of the workers:

    * [ollama-python](https://github.com/ollama/ollama-python) client:

    ```python
    import ollama

    client = ollama.Client(host="http://127.0.0.1:5001")

    # Standard request
    response = client.generate(model="mistral", prompt="Why is the sky blue?")

    # Streaming request
    for chunk in client.generate(model="mistral", prompt="Why is the sky blue?", stream=True):
        print(chunk['response'], end='', flush=True)
    ```

    For a complete Python example with streaming support, see [examples/pretty_streaming.py](examples/pretty_streaming.py).

    * [ollama-js](https://github.com/ollama/ollama-js) client:

    ```javascript
    import { Ollama } from "ollama/browser";

    const ollama = new Ollama({ host: "http://127.0.0.1:5001" });

    // Standard request
    const response = await ollama.generate({
        model: "mistral",
        prompt: "Why is the sky blue?",
    });

    // Streaming request
    const streamResponse = await ollama.generate({
        model: "mistral",
        prompt: "Why is the sky blue?",
        stream: true
    });

    for await (const chunk of streamResponse) {
        process.stdout.write(chunk.response);
    }
    ```

    For a complete TypeScript/JavaScript example with streaming support, see [examples/ts-scripts/README.md](examples/ts-scripts/README.md).

    * Raw HTTP request:

    ```sh
    curl -X POST -H "Content-Type: application/json" -L http://127.0.0.1:5001/api/generate/ \
    -d '{"model":"mistral","prompt":"Why is the sky blue?","stream":true}' \
    --no-buffer
    ```

### Decision models (System One)

`POST /v1/systemone` queues decision requests to workers and returns a single JSON response. Workers serving decision models must run Ollama **0.35 or later** and have the requested model pulled. For example, pull a model with `ollama pull tev1:4b` or `ollama pull nimble:latest`.

Use the official [Ollama Python client](https://github.com/ollama/ollama-python), which supports `client.systemone()` starting with version **0.6.3**:

```sh
uv pip install --upgrade 'ollama>=0.6.3'  # or: pip install --upgrade 'ollama>=0.6.3'
```

```python
from ollama import Client

state = {"ticket": "I was charged twice. Please refund the extra payment."}
questions = {
    "team": {
        "type": "choice",
        "instructions": "Which team should handle this ticket?",
        "criteria": {
            "billing": "Payments and refunds",
            "technical": "Bugs and integrations",
            "other": "None of the above",
        },
    },
    "refund": {
        "type": "noul",
        "instructions": "Does the customer explicitly ask for a refund?",
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this ticket?",
        "criteria": ["Routine", "Soon", "Urgent"],
    },
}

with Client(host="http://127.0.0.1:5001", timeout=180) as client:
    result = client.systemone(
        model="tev1:4b",
        state=state,
        questions=questions,
    )

print(result.answers["team"].choice)
print(result.answers["refund"].noul)
print(result.answers["urgency"].score)
```

`state` can be text, a JSON object, or an array. Responses contain named `answers`
and token `usage`. System One returns a single response and does not support
streaming.

The optional [TypeSafe Python SDK](https://pypi.org/project/typesafe-sdk/) also works. Install and configure it:

```sh
uv pip install typesafe-sdk  # or: pip install typesafe-sdk
export TYPESAFE_BASE_URL=http://127.0.0.1:5001
export TYPESAFE_API_KEY=ollama
export TYPESAFE_DEFAULT_MODEL=tev1:4b
```

Using the same `state` and `questions` from above:

```python
from typesafe_sdk import TypeSafeClient

with TypeSafeClient(timeout=180) as client:
    result = client.system_one(state=state, questions=questions)

print(result.choices["team"].choice)
```

TypeSafe's `system_one()` call uses only `/v1/systemone`. Its separate
`client.models.list()` call to `/v1/models` is not supported; use `/api/tags` to
list the workers' models. The SDK requires an API key, but oshepherd does not
authenticate it.

### Example: PyCon Austria 2025

For a practical example of how `oshepherd` can be used to orchestrate on-premise open-source LLMs, see the companion repository from the PyCon Austria 2025 talk ["Beyond the Cloud: On-Premise Orchestration for Open-Source LLMs"](https://2025.pycon.at/talks/beyond-the-cloud-on-premise-orchestration-for-open-source-llms/):

- Repository: https://github.com/mnemonica-ai/on-premise-orchestration-os-llms
- Deck: https://speakerdeck.com/p1nox/beyond-the-cloud-on-premise-orchestration-for-open-source-llms

### Disclaimers 🚨

> This package is in alpha, and its architecture and API might change in the near future. It is currently being tested by real users in a controlled environment, but it has not been audited or thoroughly tested. Use it at your own risk.
>
> As this is an alpha version, **support and responses might be limited**. We'll do our best to address questions and issues as quickly as possible.

### API server parity

- [x] **Generate a completion:** `POST /api/generate`
- [x] **Generate a chat completion:** `POST /api/chat`
- [x] **Generate Embeddings:** `POST /api/embeddings`
- [x] **Decision models:** `POST /v1/systemone`
- [x] **List Local Models:** `GET /api/tags`
- [x] **Version:** `GET /api/version`
- [x] **Show Model Information:** `POST /api/show`
- [x] **List Running Models:** `GET /api/ps`

Oshepherd supports the endpoints listed above. Official clients such as [ollama-python](https://github.com/ollama/ollama-python) and
[ollama-js](https://github.com/ollama/ollama-js) can call these routes by pointing their host at oshepherd. Support for individual request fields varies by route; this is not full Ollama API parity. For example, the Python client's `embed()` method calls `/api/embed`, which is not supported. See the [Ollama API documentation](https://github.com/ollama/ollama/blob/main/docs/api.md#api)
for the upstream API.

### Contribution guidelines

We welcome contributions! If you find a bug or have suggestions for improvements, please open an [issue](https://github.com/mnemonica-ai/oshepherd/issues) or submit a [pull request](https://github.com/mnemonica-ai/oshepherd/pulls) targeting the `development` branch. Before creating a new issue or pull request, take a moment to search the existing issues and pull requests to avoid duplicates.

##### Conda Support

To run and build locally, you can use [conda](https://conda.io/projects/conda/en/latest/user-guide/install/index.html):

```sh
conda create -n oshepherd python=3.12
conda activate oshepherd
pip install -r requirements.txt

# install oshepherd
pip install -e .
```

##### uv Support

Or you can use [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt

# install oshepherd
uv pip install -e .
```

##### Tests

The e2e tests require the following models to be available on your local Ollama instance:

```sh
ollama pull mistral        # used by generate, chat, and show tests
ollama pull embeddinggemma # used by embeddings tests
ollama pull tev1:4b         # used by System One tests; requires Ollama 0.35+
ollama pull nimble:latest   # used by System One tests; requires Ollama 0.35+
```

Then follow the usage instructions to start the API server and Celery worker, and run:

```sh
pytest -s tests/
```

The focused System One tests need no running services:

```sh
pytest -q tests/test_systemone.py
```

### Author

This is a project developed and maintained by <img src="https://prompt.mnemonica.ai/favicons/favicon-32x32-dark.png" alt="mnemonica.ai" width="16" height="16" style="vertical-align: middle;"> [mnemonica.ai](http://mnemonica.ai).

### License

[MIT](LICENSE)
