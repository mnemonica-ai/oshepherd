"""
Generate embeddings
API implementation of `POST /api/embeddings` endpoint, handling embeddings orchestration, as replica of the same Ollama server endpoint.
Ollama endpoint reference: https://github.com/ollama/ollama/blob/main/docs/api.md#generate-embeddings
"""

import time
import logging
from fastapi import Request
from oshepherd.api.utils import streamify_json
from oshepherd.api.embeddings.models import EmbeddingsRequest

logger = logging.getLogger(__name__)


def load_embeddings_routes(app):

    @app.post("/api/embeddings")
    async def embeddings(request: Request):
        from oshepherd.worker.tasks import exec_completion

        request_json = await request.json()
        logger.debug("embeddings request payload=%s", request_json)
        embeddings_request = EmbeddingsRequest(**{"payload": request_json})

        # req as json string ready to be sent through broker
        embeddings_request_json_str = embeddings_request.model_dump_json()
        logger.debug("embeddings task payload=%s", embeddings_request_json_str)

        # queue request to remote ollama api server
        task = exec_completion.delay(embeddings_request_json_str)
        task_id = task.id
        logger.info(
            "embeddings request queued task_id=%s model=%s",
            task_id,
            request_json.get("model"),
        )
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

        logger.info("ollama response received task_id=%s status=%s", task_id, status)

        return streamify_json(ollama_res, status)

    return app
