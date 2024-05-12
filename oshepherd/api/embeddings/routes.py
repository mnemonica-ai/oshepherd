"""
Generate embeddings
API implementation of `POST /api/embeddings` endpoint, handling embeddings orchestration, as replica of the same Ollama server endpoint.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#generate-embeddings
"""

import time
from flask import Blueprint, request
from oshepherd.api.utils import streamify_json
from oshepherd.api.embeddings.models import EmbeddingsRequest

embeddings_blueprint = Blueprint("embeddings", __name__, url_prefix="/api/embeddings")


@embeddings_blueprint.route("/", methods=["POST"])
def embeddings():
    from oshepherd.worker.tasks import exec_completion

    print(f" # request.json {request.json}")
    embeddings_request = EmbeddingsRequest(**{"payload": request.json})

    # req as json string ready to be sent through broker
    embeddings_request_json_str = embeddings_request.model_dump_json()
    print(f" # embeddings request {embeddings_request_json_str}")

    # queue request to remote ollama api server
    task = exec_completion.delay(embeddings_request_json_str)
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

    print(f" $ ollama response {status}: {ollama_res}")

    return streamify_json(ollama_res, status)
