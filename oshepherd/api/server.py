import signal
import uvicorn
import logging
from fastapi import FastAPI
from oshepherd.api.config import ApiConfig

logger = logging.getLogger(__name__)


def setup_api_server(app: FastAPI, config: ApiConfig) -> uvicorn.Server:
    logger.info(
        "uvicorn config host=%s port=%s workers=%s access_log=%s",
        config.HOST,
        config.PORT,
        config.WORKERS,
        config.UVICORN_ACCESS_LOG,
    )
    uvicorn_config = uvicorn.Config(
        app=app,
        host=config.HOST,
        port=config.PORT,
        workers=config.WORKERS,
        log_level=str(config.LOGLEVEL or "info").lower(),
        access_log=config.UVICORN_ACCESS_LOG,
    )
    server = uvicorn.Server(uvicorn_config)

    def graceful_shutdown(sig, frame):
        logger.info("shutting down gracefully signal=%s", sig)
        server.should_exit = True

    # Handle shutting down
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    return server
