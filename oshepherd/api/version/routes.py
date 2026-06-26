"""
Version
API implementation of `GET /api/version` endpoint, getting Ollama version from the first available Oshepherd worker.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#version
"""

import logging
from fastapi import Request
from oshepherd.api.utils import streamify_json
from oshepherd.api.network_data import NetworkData

logger = logging.getLogger(__name__)


def load_version_routes(app):
    network_data: NetworkData = app.network_data

    @app.get("/api/version")
    async def version(request: Request):
        logger.info("version request received")

        ollama_res = network_data.get_version()

        status = 200
        if ollama_res.get("error"):
            ollama_res = {
                "error": "Internal Server Error",
                "message": "error requesting version",
            }
            status = 500

        logger.info("version response status=%s", status)

        return streamify_json(ollama_res, status)

    return app
