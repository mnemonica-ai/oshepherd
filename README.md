# oshepherd

> _The Oshepherd guiding the Ollama(s) inference orchestration._

<p align="center">
  <img src="https://raw.githubusercontent.com/mnemonica-ai/oshepherd/main/assets/oshepherd_logo.png" alt="oshepherd logo" width="200">
</p>

<p align="center">
  <a href="https://pypi.org/project/oshepherd/"><img src="https://img.shields.io/pypi/v/oshepherd" alt="PyPI Version"></a>
  <a href="https://github.com/mnemonica-ai/oshepherd/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
  <a href="https://deepwiki.com/mnemonica-ai/oshepherd"><img src="https://img.shields.io/badge/DeepWiki-mnemonica--ai%2Foshepherd-blue.svg?logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACwAAAAyCAYAAAAnWDnqAAAAAXNSR0IArs4c6QAAA05JREFUaEPtmUtyEzEQhtWTQyQLHNak2AB7ZnyXZMEjXMGeK/AIi+QuHrMnbChYY7MIh8g01fJoopFb0uhhEqqcbWTp06/uv1saEDv4O3n3dV60RfP947Mm9/SQc0ICFQgzfc4CYZoTPAswgSJCCUJUnAAoRHOAUOcATwbmVLWdGoH//PB8mnKqScAhsD0kYP3j/Yt5LPQe2KvcXmGvRHcDnpxfL2zOYJ1mFwrryWTz0advv1Ut4CJgf5uhDuDj5eUcAUoahrdY/56ebRWeraTjMt/00Sh3UDtjgHtQNHwcRGOC98BJEAEymycmYcWwOprTgcB6VZ5JK5TAJ+fXGLBm3FDAmn6oPPjR4rKCAoJCal2eAiQp2x0vxTPB3ALO2CRkwmDy5WohzBDwSEFKRwPbknEggCPB/imwrycgxX2NzoMCHhPkDwqYMr9tRcP5qNrMZHkVnOjRMWwLCcr8ohBVb1OMjxLwGCvjTikrsBOiA6fNyCrm8V1rP93iVPpwaE+gO0SsWmPiXB+jikdf6SizrT5qKasx5j8ABbHpFTx+vFXp9EnYQmLx02h1QTTrl6eDqxLnGjporxl3NL3agEvXdT0WmEost648sQOYAeJS9Q7bfUVoMGnjo4AZdUMQku50McDcMWcBPvr0SzbTAFDfvJqwLzgxwATnCgnp4wDl6Aa+Ax283gghmj+vj7feE2KBBRMW3FzOpLOADl0Isb5587h/U4gGvkt5v60Z1VLG8BhYjbzRwyQZemwAd6cCR5/XFWLYZRIMpX39AR0tjaGGiGzLVyhse5C9RKC6ai42ppWPKiBagOvaYk8lO7DajerabOZP46Lby5wKjw1HCRx7p9sVMOWGzb/vA1hwiWc6jm3MvQDTogQkiqIhJV0nBQBTU+3okKCFDy9WwferkHjtxib7t3xIUQtHxnIwtx4mpg26/HfwVNVDb4oI9RHmx5WGelRVlrtiw43zboCLaxv46AZeB3IlTkwouebTr1y2NjSpHz68WNFjHvupy3q8TFn3Hos2IAk4Ju5dCo8B3wP7VPr/FGaKiG+T+v+TQqIrOqMTL1VdWV1DdmcbO8KXBz6esmYWYKPwDL5b5FA1a0hwapHiom0r/cKaoqr+27/XcrS5UwSMbQAAAABJRU5ErkJggg==" alt="DeepWiki"></a>
</p>

A centralized [FastAPI](https://fastapi.tiangolo.com/) service, using [Celery](https://docs.celeryq.dev) and [Redis](https://redis.com) to orchestrate multiple [Ollama](https://ollama.com) servers as workers.

### Install

```sh
pip install oshepherd
```

### Usage

1. Setup Redis:

    [Celery](https://docs.celeryq.dev) uses [Redis](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/index.html#redis) as message broker and backend. You'll need a Redis instance, which you can provision for free in [redislabs.com](https://app.redislabs.com).

2. Setup FastAPI Server:

    ```sh
    # define configuration env file
    # use credentials for redis as broker and backend
    cp .api.env.template .api.env

    # start api
    oshepherd start-api --env-file .api.env
    ```

3. Setup Celery/Ollama Worker(s):

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

4. Now you're ready to execute Ollama completions remotely. You can point your Ollama client to your oshepherd api server by setting the `host`, and it will return your requested completions from any of the workers:

    * [ollama-python](https://github.com/ollama/ollama-python) client:

    ```python
    import ollama

    client = ollama.Client(host="http://127.0.0.1:5001")
    ollama_response = client.generate({"model": "mistral", "prompt": "Why is the sky blue?"})
    ```

    * [ollama-js](https://github.com/ollama/ollama-js) client:

    ```javascript
    import { Ollama } from "ollama/browser";

    const ollama = new Ollama({ host: "http://127.0.0.1:5001" });
    const ollamaResponse = await ollama.generate({
        model: "mistral",
        prompt: "Why is the sky blue?",
    });
    ```

    * Raw http request:

    ```sh
    curl -X POST -H "Content-Type: application/json" -L http://127.0.0.1:5001/api/generate/ -d '{
        "model": "mistral",
        "prompt":"Why is the sky blue?"
    }'
    ```

### Streaming Support

Oshepherd now supports **streaming responses** for both `/api/generate` and `/api/chat` endpoints, allowing real-time token-by-token responses from Ollama workers.

#### How it works

1. **Client** sends request with `stream: true`
2. **API** submits task to Celery and subscribes to Redis Pub/Sub channel
3. **Worker** executes Ollama inference with streaming enabled
4. **Worker** publishes each response chunk to Redis Pub/Sub in real-time
5. **API** streams chunks back to client as newline-delimited JSON (NDJSON)

#### Architecture

- **Task Distribution**: Celery still handles task orchestration and worker selection
- **Streaming Transport**: Redis Pub/Sub enables real-time chunk delivery from worker to API
- **Compatibility**: Fully compatible with Ollama's streaming format

#### Example Usage

* **Python (ollama-python)**:

```python
import ollama

client = ollama.Client(host="http://127.0.0.1:5001")

# Streaming generate
for chunk in client.generate(model="mistral", prompt="Why is the sky blue?", stream=True):
    print(chunk['response'], end='', flush=True)

# Streaming chat
for chunk in client.chat(model="mistral", messages=[{"role": "user", "content": "Hello!"}], stream=True):
    print(chunk['message']['content'], end='', flush=True)
```

* **JavaScript (ollama-js)**:

```javascript
import { Ollama } from "ollama/browser";

const ollama = new Ollama({ host: "http://127.0.0.1:5001" });

// Streaming generate
const response = await ollama.generate({
    model: "mistral",
    prompt: "Why is the sky blue?",
    stream: true
});

for await (const chunk of response) {
    process.stdout.write(chunk.response);
}
```

* **Raw HTTP**:

```sh
curl -X POST -H "Content-Type: application/json" -L http://127.0.0.1:5001/api/generate/ -d '{
    "model": "mistral",
    "prompt": "Why is the sky blue?",
    "stream": true
}'
```

**Note**: When `stream: false` or omitted, the API falls back to the original non-streaming behavior (blocking until full response is ready).

### Disclaimers 🚨

> This package is in alpha, its architecture and api might change in the near future. Currently this is getting tested in a controlled environment by real users, but haven't been audited, nor tested thorugly. Use it at your own risk.
>
> As this is an alpha version, **support and responses might be limited**. We'll do our best to address questions and issues as quickly as possible.

### API server parity

- [x] **Generate a completion:** `POST /api/generate`
- [x] **Generate a chat completion:** `POST /api/chat`
- [x] **Generate Embeddings:** `POST /api/embeddings`
- [x] **List Local Models:** `GET /api/tags`
- [x] **Version:** `GET /api/version`
- [ ] **Show Model Information:** `POST /api/show` (pending)
- [ ] **List Running Models:** `GET /api/ps` (pending)

Oshepherd API server has been designed to maintain compatibility with the endpoints defined by Ollama, ensuring that any official client (i.e.: [ollama-python](https://github.com/ollama/ollama-python), [ollama-js](https://github.com/ollama/ollama-js)) can use this server as host and receive expected responses. For more details on the full API specifications, refer to the official [Ollama API documentation](https://github.com/ollama/ollama/blob/main/docs/api.md#api).

### Contribution guidelines

We welcome contributions! If you find a bug or have suggestions for improvements, please open an [issue](https://github.com/mnemonica-ai/oshepherd/issues) or submit a [pull request](https://github.com/mnemonica-ai/oshepherd/pulls) pointing to `development` branch. Before creating a new issue/pull request, take a moment to search through the existing issues/pull requests to avoid duplicates.

##### Conda Support

To run and build locally you can use [conda](https://conda.io/projects/conda/en/latest/user-guide/install/index.html):

```sh
conda create -n oshepherd python=3.12
conda activate oshepherd
pip install -r requirements.txt

# install oshepherd
pip install -e .
```

##### Tests

Follow usage instructions to start api server and celery worker using a local ollama, and then run the tests:

```sh
pytest -s tests/
```

### Author

This is a project developed and maintained by [mnemonica.ai](mnemonica.ai).

### License

[MIT](LICENSE)
