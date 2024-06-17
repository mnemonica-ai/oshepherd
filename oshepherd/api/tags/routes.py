"""
Returns the list of models available across workers
API implementation of the `GET /api/tags` endpoint, which returns the list models that are available locally
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#list-local-models
"""

import time
from flask import Blueprint
from oshepherd.api.utils import streamify_json

tags_blueprint = Blueprint("tags", __name__, url_prefix="/api/tags")


@tags_blueprint.route("/", methods=["GET"])
def tags():
    from oshepherd.worker.tasks import exec_completion

    print(" # tags request")

    # queue request to remote ollama api server
    task = exec_completion.delay('')
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
