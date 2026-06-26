"""
Tags
API implementation of `POST /api/tags` endpoint, list models that are available in any Oshepherd worker.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#list-local-models
"""

import logging
from fastapi import Request
from oshepherd.api.utils import streamify_json
from oshepherd.api.network_data import NetworkData

logger = logging.getLogger(__name__)


def load_tags_routes(app):
    network_data: NetworkData = app.network_data

    @app.get("/api/tags")
    async def tags(request: Request):
        logger.info("tags request received")

        ollama_res = network_data.get_available_tags()
        status = 200
        if ollama_res.get("error"):
            ollama_res = {
                "error": "Internal Server Error",
                "message": "error requesting tags",
            }
            status = 500

        logger.info("tags response status=%s", status)

        return streamify_json(ollama_res, status)

    return app
