from fastapi import FastAPI
from oshepherd.api.config import ApiConfig
from oshepherd.worker.app import create_celery_app_for_fastapi
from oshepherd.api.generate.routes import load_generate_routes
from oshepherd.api.embeddings.routes import load_embeddings_routes
from oshepherd.api.chat.routes import load_chat_routes


def setup_api_app(config: ApiConfig) -> FastAPI:
    app = FastAPI()

    celery_app = create_celery_app_for_fastapi(config)
    print(" * celery_app ready: ", celery_app)

    @app.get("/health")
    async def health():
        return {"status": 200}

    load_generate_routes(app)
    load_embeddings_routes(app)
    load_chat_routes(app)

    return app
