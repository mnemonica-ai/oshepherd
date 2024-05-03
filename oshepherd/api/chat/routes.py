"""
Generate a chat completion
API implementation of `POST /api/chat` endpoint, handling completion orchestration, as replica of the same Ollama server endpoint.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#generate-a-chat-completion
"""

import time
from flask import Blueprint, request
from oshepherd.api.utils import streamify_json
from oshepherd.api.chat.models import ChatRequest

chat_blueprint = Blueprint("chat", __name__, url_prefix="/api/chat")


@chat_blueprint.route("/", methods=["POST"])
def chat():
    from oshepherd.worker.tasks import exec_completion

    print(f" # request.json {request.json}")
    chat_request = ChatRequest(**{"payload": request.json})

    # req as json string ready to be sent through broker
    chat_request_json_str = chat_request.model_dump_json()
    print(f" # chat request {chat_request_json_str}")

    # queue request to remote ollama api server
    task = exec_completion.delay(chat_request_json_str)
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
