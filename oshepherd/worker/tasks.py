import json
import ollama
from oshepherd.worker.app import celery_app
from oshepherd.worker.ollama_task import OllamaCeleryTask


@celery_app.task(
    name="oshepherd.worker.tasks.exec_completion",
    bind=True,
    base=OllamaCeleryTask,
)
def exec_completion(self, request_str: str):
    try:
        request = json.loads(request_str)
        print(f"# exec_completion request {request}")
        req_type = request["type"]
        req_payload = request["payload"]

        if req_type == "generate":
            response = ollama.generate(**req_payload)
        elif req_type == "chat":
            response = ollama.chat(**req_payload)
        elif req_type == "embeddings":
            response = ollama.embeddings(**req_payload)

        # Convert response objects to dict for JSON serialization
        if hasattr(response, "model_dump"):
            serializable_response = response.model_dump()
        elif hasattr(response, "__dict__"):
            serializable_response = dict(response)
        else:
            serializable_response = response

        print(f"  $ success {serializable_response}")
    except Exception as error:
        print(f"  * error exec_completion {error}")
        serializable_response = {
            "error": {"type": str(error.__class__.__name__), "message": str(error)}
        }
        print(
            f"    * error response {serializable_response}",
        )

        raise

    return serializable_response
