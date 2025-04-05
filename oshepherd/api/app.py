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
import logging

# disable uvicorn access logs
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def setup_api_app(config: ApiConfig) -> FastAPI:
    app = FastAPI()

    celery_app = create_celery_app_for_fastapi(config)
    print(" * celery_app ready: ", celery_app)

    network_data = NetworkData(config)
    print(" * network_data ready: ", network_data)
    app.network_data = network_data

    load_health_routes(app)
    load_version_routes(app)
    load_generate_routes(app)
    load_embeddings_routes(app)
    load_chat_routes(app)
    load_tags_routes(app)

    return app
