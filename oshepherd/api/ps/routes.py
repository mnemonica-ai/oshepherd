"""
List Running Models
API implementation of `GET /api/ps` endpoint, listing models currently loaded in memory across all Oshepherd workers.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#list-running-models
"""

import logging
from fastapi import Request
from oshepherd.api.utils import streamify_json
from oshepherd.api.network_data import NetworkData

logger = logging.getLogger(__name__)


def load_ps_routes(app):
    network_data: NetworkData = app.network_data

    @app.get("/api/ps")
    async def ps(request: Request):
        logger.info("ps request received")

        ollama_res = network_data.get_running_models()
        status = 200
        if ollama_res.get("error"):
            ollama_res = {
                "error": "Internal Server Error",
                "message": "error requesting running models",
            }
            status = 500

        logger.info("ps response status=%s", status)

        return streamify_json(ollama_res, status)

    return app
