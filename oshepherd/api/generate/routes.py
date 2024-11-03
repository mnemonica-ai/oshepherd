"""
Generate a completion
API implementation of `POST /api/generate` endpoint, handling completion orchestration, as replica of the same Ollama server endpoint.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#generate-a-completion
"""

import time
from fastapi import Request
from oshepherd.api.utils import streamify_json
from oshepherd.api.generate.models import GenerateRequest


def load_generate_routes(app):

    @app.post("/api/generate")
    async def generate(request: Request):
        from oshepherd.worker.tasks import exec_completion

        request_json = await request.json()
        print(f" # request json: {request_json}")
        generate_request = GenerateRequest(**{"payload": request_json})

        # req as json string ready to be sent through broker
        generate_request_json_str = generate_request.model_dump_json()
        print(f" # generate request: {generate_request_json_str}")

        # queue request to remote ollama api server
        task = exec_completion.delay(generate_request_json_str)
        while not task.ready():
            print(" > waiting for response...")
            time.sleep(1)
        ollama_res = task.get(timeout=1)

        status = 200
        if ollama_res.get("error"):
            ollama_res = {
                "error": "Internal Server Error",
                "message": f"error executing completion: {ollama_res['error']['message']}",
            }
            status = 500

        print(f" $ ollama response [{status}]: {ollama_res}")

        return streamify_json(ollama_res, status)

    return app
