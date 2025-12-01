import json
import ollama
from oshepherd.worker.app import celery_app
from oshepherd.worker.ollama_task import OllamaCeleryTask
from oshepherd.common.ollama import serialize_ollama_res
from oshepherd.common.redis_service import RedisService
from oshepherd.worker.config import WorkerConfig
from oshepherd.common.lib import load_and_validate_env


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
        task_id = self.request.id
        is_streaming = req_payload.get("stream", False)

        # Initialize Redis service for streaming
        redis_service = None
        if is_streaming:
            config = load_and_validate_env(WorkerConfig)
            redis_service = RedisService(config.CELERY_BACKEND_URL)
            stream_channel = f"oshepherd:stream:{task_id}"
            print(f" > Streaming enabled on channel: {stream_channel}")

        if req_type == "generate":
            if is_streaming:
                # Stream responses via Redis Pub/Sub
                for chunk in ollama.generate(**req_payload):
                    serializable_chunk = serialize_ollama_res(chunk)
                    redis_service.publish(
                        stream_channel, json.dumps(serializable_chunk)
                    )
                    # print(f"  > Sending chunk [{task_id}]: done={serializable_chunk.get('done')}")

                # Return success indicator for the celery task
                serializable_response = {"status": "streaming_completed"}
            else:
                response = ollama.generate(**req_payload)
                serializable_response = serialize_ollama_res(response)

        elif req_type == "chat":
            if is_streaming:
                # Stream responses via Redis Pub/Sub
                for chunk in ollama.chat(**req_payload):
                    serializable_chunk = serialize_ollama_res(chunk)
                    redis_service.publish(
                        stream_channel, json.dumps(serializable_chunk)
                    )
                    # print(f"  > Sending chunk [{task_id}]: done={serializable_chunk.get('done')}")

                # Return success indicator for the celery task
                serializable_response = {"status": "streaming_completed"}
            else:
                response = ollama.chat(**req_payload)
                serializable_response = serialize_ollama_res(response)

        elif req_type == "embeddings":
            response = ollama.embeddings(**req_payload)
            serializable_response = serialize_ollama_res(response)

        elif req_type == "show":
            response = ollama.show(**req_payload)
            serializable_response = serialize_ollama_res(response)

        print(f"  $ success {serializable_response}")
    except Exception as error:
        print(f"  * error exec_completion {error}")
        serializable_response = {
            "error": {"type": str(error.__class__.__name__), "message": str(error)}
        }
        print(
            f"    * error response {serializable_response}",
        )

        if is_streaming and redis_service:
            try:
                error_chunk = {
                    "error": serializable_response["error"]["message"],
                    "done": True,
                }
                redis_service.publish(stream_channel, json.dumps(error_chunk))
            except Exception as publish_error:
                print(f"  * error publishing error to stream: {publish_error}")

        raise

    return serializable_response
