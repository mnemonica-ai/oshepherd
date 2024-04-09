import time
from flask import Blueprint, request
from oshepherd.api.utils import streamify_json
from oshepherd.api.generate.models import GenerateRequest

generate_blueprint = Blueprint("generate", __name__, url_prefix="/api/generate")


@generate_blueprint.route("/", methods=["POST"])
def generate():
    # TODO use .send_task() instead?
    from oshepherd.worker.tasks import make_generate_request

    print(f" # request.json {request.json}")
    generate_request = GenerateRequest(**request.json)

    # req as json string ready to be sent though broker
    generate_request_json_str = generate_request.model_dump_json()
    print(generate_request_json_str)

    # queue request to remote ollama api server though
    task = make_generate_request.delay(generate_request_json_str)
    while not task.ready():
        print(" > waiting for response...")
        time.sleep(1)
    ollama_res = task.get(timeout=1)

    status = 200
    if ollama_res.get("error"):
        ollama_res = {
            "error": "Internal Server Error",
            "message": f"error triggering llm inference: {ollama_res['error']['message']}",
        }
        status = 500

    print(f" $ ollama response {status}: {ollama_res}")

    return streamify_json(ollama_res, status)
