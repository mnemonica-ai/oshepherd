"""
Show Model Information
API implementation of `POST /api/show` endpoint, showing information about a model.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#show-model-information
"""

import time
from fastapi import Request
from oshepherd.api.utils import streamify_json
from oshepherd.api.show.models import ShowRequest


def load_show_routes(app):

    @app.post("/api/show")
    async def show(request: Request):
        from oshepherd.worker.tasks import exec_completion

        request_json = await request.json()
        print(f" # show request: {request_json}")
        show_request = ShowRequest(**request_json)

        # Prepare request for celery task
        task_request = {
            "type": "show",
            "payload": show_request.model_dump(),
        }
        task_request_json_str = str(task_request).replace("'", '"')
        print(f" # show task request: {task_request_json_str}")

        # Queue request to remote ollama api server
        task = exec_completion.delay(task_request_json_str)
        task_id = task.id
        print(f" > Celery task created: {task_id}")

        # Wait for task completion
        while not task.ready():
            print(" > waiting for show response...")
            time.sleep(1)
        ollama_res = task.get(timeout=1)

        status = 200
        if ollama_res.get("error"):
            ollama_res = {
                "error": "Internal Server Error",
                "message": f"error showing model: {ollama_res['error']['message']}",
            }
            status = 500

        print(f" $ ollama response [{status}]: {ollama_res}")

        return streamify_json(ollama_res, status)

    return app
