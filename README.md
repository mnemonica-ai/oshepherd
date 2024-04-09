# oshepherd

> The Oshepherd guiding the Ollama(s) inference orchestration.

A centralized [Flask](https://flask.palletsprojects.com) API service, using [Celery](https://docs.celeryq.dev) ([RabbitMQ](https://www.rabbitmq.com) + [Redis](https://redis.com)) to orchestrate multiple [Ollama](https://ollama.com) workers.

### Install

```
pip install oshepherd
```

### Usage

1. Setup RabbitMQ and Redis:

    Create instances for free for both:
        * [cloudamqp.com](https://www.cloudamqp.com)
        * [redislabs.com](https://app.redislabs.com)

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

### Words of advice 🚨

This package is in alpha, its architecture and api might change in the near future. Currently this is getting tested in a closed environment by real users, but haven't been audited, nor tested thorugly. Use it at your own risk.

### Disclaimer on Support

As this is an alpha version, support and responses might be limited. We'll do our best to address questions and issues as quickly as possible.

### Contribution Guidelines

We welcome contributions! If you find a bug or have suggestions for improvements, please open an issue or submit a pull request.

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

### Reporting Issues

Please report any issues you encounter on the GitHub issues page. Before creating a new issue, take a moment to search through the existing issues to avoid duplicates.

### Author

Currently, [mnemonica.ai](mnemonica.ai) is sponsoring the development of this tool.

### License

[MIT](LICENSE)
