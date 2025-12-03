"""
Show Model Information
API implementation of `POST /api/show` endpoint, showing information about a model.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#show-model-information
"""

from fastapi import Request
from oshepherd.api.utils import streamify_json
from oshepherd.api.show.models import ShowRequest
from oshepherd.api.network_data import NetworkData


def load_show_routes(app):
    network_data: NetworkData = app.network_data

    @app.post("/api/show")
    async def show(request: Request):
        request_json = await request.json()
        print(f" # show request: {request_json}")
        show_request = ShowRequest(**request_json)

        ollama_res = network_data.get_model_info(show_request.model)

        status = 200
        if ollama_res.get("error"):
            status = (
                404 if "not found" in ollama_res.get("message", "").lower() else 500
            )

        print(f" $ ollama response [{status}]: {ollama_res}")

        return streamify_json(ollama_res, status)

    return app
