"""
Generate a completion
API implementation of `POST /api/generate` endpoint, handling completion orchestration, as replica of the same Ollama server endpoint.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#generate-a-completion
"""

import time
import json
import logging
from fastapi import Request
from fastapi.responses import StreamingResponse
from oshepherd.api.utils import streamify_json
from oshepherd.api.generate.models import GenerateRequest
from oshepherd.common.redis_service import RedisService

logger = logging.getLogger(__name__)


def load_generate_routes(app):

    @app.post("/api/generate")
    async def generate(request: Request):
        from oshepherd.worker.tasks import exec_completion

        request_json = await request.json()
        logger.debug("generate request payload=%s", request_json)
        generate_request = GenerateRequest(**{"payload": request_json})

        # req as json string ready to be sent through broker
        generate_request_json_str = generate_request.model_dump_json()
        logger.debug("generate task payload=%s", generate_request_json_str)

        is_streaming = request_json.get("stream", False)

        # queue request to remote ollama api server
        task = exec_completion.delay(generate_request_json_str)
        task_id = task.id
        logger.info(
            "generate request queued task_id=%s stream=%s model=%s",
            task_id,
            is_streaming,
            request_json.get("model"),
        )

        if is_streaming:
            logger.info("streaming mode enabled task_id=%s", task_id)

            def stream_generator():
                redis_service = RedisService(app.celery_app.conf.broker_url)
                stream_channel = f"oshepherd:stream:{task_id}"
                logger.debug(
                    "subscribing to stream channel task_id=%s channel=%s",
                    task_id,
                    stream_channel,
                )

                try:
                    for chunk in redis_service.subscribe_to_channel(stream_channel):
                        # Each chunk is already a dict from Redis
                        chunk_json = json.dumps(chunk) + "\n"
                        # Send worker chunk response to client
                        yield chunk_json.encode("utf-8")

                        # Break after receiving the final chunk
                        if chunk.get("done") is True:
                            logger.info("stream completed task_id=%s", task_id)
                            break
                except Exception as e:
                    logger.exception("error streaming response task_id=%s", task_id)
                    error_response = {
                        "error": f"Streaming error: {str(e)}",
                        "done": True,
                    }
                    yield (json.dumps(error_response) + "\n").encode("utf-8")

            return StreamingResponse(
                stream_generator(),
                media_type="application/x-ndjson",
                status_code=200,
            )
        else:
            logger.info("celery mode enabled task_id=%s", task_id)

            while not task.ready():
                logger.debug("waiting for response task_id=%s", task_id)
                time.sleep(1)
            ollama_res = task.get(timeout=1)

            status = 200
            if ollama_res.get("error"):
                ollama_res = {
                    "error": "Internal Server Error",
                    "message": f"error executing completion: {ollama_res['error']['message']}",
                }
                status = 500

            logger.info(
                "ollama response received task_id=%s status=%s", task_id, status
            )

            return streamify_json(ollama_res, status)

    return app
