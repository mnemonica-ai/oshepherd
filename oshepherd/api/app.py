from fastapi import FastAPI
from oshepherd.api.config import ApiConfig
from oshepherd.worker.app import create_celery_app_for_fastapi
from oshepherd.api.network_data import NetworkData
from oshepherd.api.health import load_health_routes
from oshepherd.api.version.routes import load_version_routes
from oshepherd.api.generate.routes import load_generate_routes
from oshepherd.api.embeddings.routes import load_embeddings_routes
from oshepherd.api.chat.routes import load_chat_routes
from oshepherd.api.tags.routes import load_tags_routes
from oshepherd.api.show.routes import load_show_routes
from oshepherd.api.ps.routes import load_ps_routes
import logging

logger = logging.getLogger(__name__)


def setup_api_app(config: ApiConfig) -> FastAPI:
    app = FastAPI()

    celery_app = create_celery_app_for_fastapi(config)
    logger.info("celery app ready")
    app.celery_app = celery_app

    network_data = NetworkData(config)
    logger.info("network data ready")
    app.network_data = network_data

    load_health_routes(app)
    load_version_routes(app)
    load_generate_routes(app)
    load_embeddings_routes(app)
    load_chat_routes(app)
    load_tags_routes(app)
    load_show_routes(app)
    load_ps_routes(app)

    return app
