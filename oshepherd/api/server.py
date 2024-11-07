import signal
import uvicorn
from fastapi import FastAPI
from oshepherd.api.config import ApiConfig


def setup_api_server(app: FastAPI, config: ApiConfig) -> uvicorn.Server:
    print(
        f" * uvicorn config: [host:{config.HOST}:{config.PORT}] [workers:{config.WORKERS}]"
    )
    uvicorn_config = uvicorn.Config(
        app=app, host=config.HOST, port=config.PORT, workers=config.WORKERS
    )
    server = uvicorn.Server(uvicorn_config)

    def graceful_shutdown(sig, frame):
        print("Shutting down gracefully...")
        server.should_exit = True

    # Handle shutting down
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    return server
