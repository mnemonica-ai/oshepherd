import json
import ollama
from oshepherd.worker.app import celery_app


@celery_app.task(name="oshepherd.worker.tasks.make_generate_request")
def make_generate_request(request_str: str):
    try:
        request = json.loads(request_str)
        print(f"# make_generate_request request {request}")

        response = ollama.generate(**request)
        print(f"  $ success {response}")
    except Exception as error:
        print(f"  * error make_generate_request {error}")
        response = {
            "error": {"type": str(error.__class__.__name__), "message": str(error)}
        }
        print(
            f"    * error response {response}",
        )

    return response
