from fastapi import Request
from oshepherd.api.utils import streamify_json
from oshepherd.api.network_data import NetworkData


def load_version_routes(app):
    network_data: NetworkData = app.network_data

    @app.get("/api/version")
    async def version(request: Request):
        print(f" # version request")

        ollama_res = network_data.get_version()
        status = 200
        if ollama_res.get("error"):
            ollama_res = {
                "error": "Internal Server Error",
                "message": "error requesting tags",
            }
            status = 500

        print(f" $ ollama response [{status}]: {ollama_res}")

        return streamify_json(ollama_res, status)

    return app
