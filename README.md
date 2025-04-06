# oshepherd

> _The Oshepherd guiding the Ollama(s) inference orchestration._

<p align="center">
  <img src="https://raw.githubusercontent.com/mnemonica-ai/oshepherd/main/assets/oshepherd_logo.png" alt="oshepherd logo" width="200">
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
conda create -n oshepherd python=3.8
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
