"""Queue non-streaming System One decision requests to an Ollama worker."""

import logging

from fastapi.concurrency import run_in_threadpool

from oshepherd.api.systemone.models import SystemOneRequest, SystemOneRequestPayload
from oshepherd.api.utils import streamify_json

logger = logging.getLogger(__name__)


def load_systemone_routes(app):
    @app.post("/v1/systemone")
    async def systemone(payload: SystemOneRequestPayload):
        from oshepherd.worker.tasks import exec_completion

        # This endpoint never uses Redis Pub/Sub, even if an extra stream field
        # is supplied. Ollama validates question contents and extra parameters.
        request = SystemOneRequest(payload=payload)
        task = exec_completion.delay(request.model_dump_json())
        logger.info(
            "systemone request queued task_id=%s model=%s", task.id, payload.model
        )

        try:
            # Celery waits synchronously; keep the API's event loop available.
            response = await run_in_threadpool(task.get)
        except Exception as error:
            logger.exception("systemone task failed task_id=%s", task.id)
            response = {"error": {"message": str(error)}}

        if response.get("error"):
            return streamify_json(
                {
                    "error": "Internal Server Error",
                    "message": f"error executing completion: {response['error']['message']}",
                },
                500,
            )

        logger.info("systemone response received task_id=%s", task.id)
        return streamify_json(response)

    return app
