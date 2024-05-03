# oshepherd

> _The Oshepherd guiding the Ollama(s) inference orchestration._

A centralized [Flask](https://flask.palletsprojects.com) API service, using [Celery](https://docs.celeryq.dev) ([RabbitMQ](https://www.rabbitmq.com) + [Redis](https://redis.com)) to orchestrate multiple [Ollama](https://ollama.com) servers as workers.

### Install

```
pip install oshepherd
```

### Usage

1. Setup RabbitMQ and Redis:

    [Celery](https://docs.celeryq.dev) uses [RabbitMQ](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/index.html#rabbitmq) as message broker, and [Redis](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/index.html#redis) as backend, you'll need to create one instance for each. You can create small instances for free in [cloudamqp.com](https://www.cloudamqp.com) and [redislabs.com](https://app.redislabs.com) respectively.

2. Setup Flask API Server:

    ```
    # define configuration env file
    #   use credentials for redis and rabbitmq
    cp .api.env.template .api.env

    # start api
    oshepherd start-api --env-file .api.env
    ```

3. Setup Celery/Ollama Worker(s):

    ```
    # install ollama https://ollama.com/download
    ollama run mistral

    # define configuration env file
    #   use credentials for redis and rabbitmq
    cp .worker.env.template .worker.env

    # start worker
    oshepherd start-worker --env-file .worker.env
    ```

4. Done, now you're ready to execute Ollama completions remotely. You can point your Ollama client to your oshepherd api server by setting the `host`, and it will return your requested completions from any of the workers:

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

### Words of advice 🚨

This package is in alpha, its architecture and api might change in the near future. Currently this is getting tested in a controlled environment by real users, but haven't been audited, nor tested thorugly. Use it at your own risk.

### Disclaimer on Support

As this is an alpha version, support and responses might be limited. We'll do our best to address questions and issues as quickly as possible.

### Contribution Guidelines

We welcome contributions! If you find a bug or have suggestions for improvements, please open an [issue](https://github.com/mnemonica-ai/oshepherd/issues) or submit a [pull request](https://github.com/mnemonica-ai/oshepherd/pulls). Before creating a new issue/pull request, take a moment to search through the existing issues/pull requests to avoid duplicates.

##### Conda Support

To run and build locally you can use [conda](https://conda.io/projects/conda/en/latest/user-guide/install/index.html):

```
conda create -n oshepherd python=3.8
conda activate oshepherd
pip install -r requirements.txt

# install oshepherd
pip install -e .
```

##### Tests

Follow usage instructions to start api server and celery worker using a local ollama, and then run the tests:

```
pytest -s tests/
```

### Author

This is a project developed and maintained by [mnemonica.ai](mnemonica.ai).

### License

[MIT](LICENSE)
