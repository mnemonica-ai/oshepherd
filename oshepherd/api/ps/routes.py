"""
List Running Models
API implementation of `GET /api/ps` endpoint, listing models currently loaded in memory across all Oshepherd workers.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#list-running-models
"""

from fastapi import Request
from oshepherd.api.utils import streamify_json
from oshepherd.api.network_data import NetworkData


def load_ps_routes(app):
    network_data: NetworkData = app.network_data

    @app.get("/api/ps")
    async def ps(request: Request):
        print(" # ps request")

        ollama_res = network_data.get_running_models()
        status = 200
        if ollama_res.get("error"):
            ollama_res = {
                "error": "Internal Server Error",
                "message": "error requesting running models",
            }
            status = 500

        print(f" $ ollama response [{status}]: {ollama_res}")

        return streamify_json(ollama_res, status)

    return app
