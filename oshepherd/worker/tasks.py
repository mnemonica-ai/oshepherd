import json
import logging
import ollama
from oshepherd.worker.app import celery_app
from oshepherd.worker.ollama_task import OllamaCeleryTask
from oshepherd.common.ollama import serialize_ollama_res
from oshepherd.common.redis_service import RedisService
from oshepherd.worker.config import WorkerConfig
from oshepherd.common.lib import load_and_validate_env

logger = logging.getLogger(__name__)


@celery_app.task(
    name="oshepherd.worker.tasks.exec_completion",
    bind=True,
    base=OllamaCeleryTask,
)
def exec_completion(self, request_str: str):
    is_streaming = False
    redis_service = None
    stream_channel = None
    task_id = self.request.id
    try:
        request = json.loads(request_str)
        logger.debug("exec_completion request task_id=%s payload=%s", task_id, request)
        req_type = request["type"]
        req_payload = request["payload"]
        is_streaming = req_payload.get("stream", False)
        logger.info(
            "exec_completion started task_id=%s type=%s stream=%s model=%s",
            task_id,
            req_type,
            is_streaming,
            req_payload.get("model"),
        )

        # Initialize Redis service for streaming
        if is_streaming:
            config = load_and_validate_env(WorkerConfig)
            redis_service = RedisService(config.CELERY_BACKEND_URL)
            stream_channel = f"oshepherd:stream:{task_id}"
            logger.debug(
                "streaming enabled task_id=%s channel=%s", task_id, stream_channel
            )

        if req_type == "generate":
            if is_streaming:
                # Stream responses via Redis Pub/Sub
                for chunk in ollama.generate(**req_payload):
                    serializable_chunk = serialize_ollama_res(chunk)
                    redis_service.publish(
                        stream_channel, json.dumps(serializable_chunk)
                    )

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

                # Return success indicator for the celery task
                serializable_response = {"status": "streaming_completed"}
            else:
                response = ollama.chat(**req_payload)
                serializable_response = serialize_ollama_res(response)

        elif req_type == "embeddings":
            # ponytail: use deprecated /api/embeddings instead of /api/embed — newer endpoint requires --embeddings server flag
            response = ollama.embeddings(
                model=req_payload["model"],
                prompt=req_payload.get("input", ""),
                options=req_payload.get("options"),
                keep_alive=req_payload.get("keep_alive"),
            )
            serializable_response = serialize_ollama_res(response)
        else:
            raise ValueError(f"Unsupported request type: {req_type}")

        logger.info("exec_completion completed task_id=%s type=%s", task_id, req_type)
    except Exception as error:
        logger.exception(
            "exec_completion failed task_id=%s error_type=%s error=%s",
            task_id,
            error.__class__.__name__,
            error,
        )
        serializable_response = {
            "error": {"type": str(error.__class__.__name__), "message": str(error)}
        }

        if is_streaming and redis_service:
            try:
                error_chunk = {
                    "error": serializable_response["error"]["message"],
                    "done": True,
                }
                redis_service.publish(stream_channel, json.dumps(error_chunk))
            except Exception as publish_error:
                logger.exception(
                    "error publishing failure to stream task_id=%s error=%s",
                    task_id,
                    publish_error,
                )

        raise

    return serializable_response
