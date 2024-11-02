from flask import Flask, Blueprint
from oshepherd.api.config import ApiConfig
from oshepherd.api.generate.routes import generate_blueprint
from oshepherd.api.chat.routes import chat_blueprint
from oshepherd.api.embeddings.routes import embeddings_blueprint
from oshepherd.worker.app import create_celery_app_for_fastapi

from fastapi import FastAPI, Request
import time
from flask import Blueprint, request
from oshepherd.api.utils import streamify_json
from oshepherd.api.generate.models import GenerateRequest

def start_api_app(config: ApiConfig):
    app = FastAPI()

    # celery setup
    celery_app = create_celery_app_for_fastapi(config)
    # app.celery = celery_app

    @app.get("/")
    async def home():
        return {"status": 200}
    
    @app.post("/api/generate")
    async def generate(request: Request):
        from oshepherd.worker.tasks import exec_completion

        request_data = await request.json()
        print(f" # request.json {request_data}")
        generate_request = GenerateRequest(**{"payload": request_data})

        # req as json string ready to be sent through broker
        generate_request_json_str = generate_request.model_dump_json()
        print(f" # generate request {generate_request_json_str}")

        # queue request to remote ollama api server
        task = exec_completion.delay(generate_request_json_str)
        while not task.ready():
            print(" > waiting for response...")
            time.sleep(1)
        ollama_res = task.get(timeout=1)

        status = 200
        if ollama_res.get("error"):
            ollama_res = {
                "error": "Internal Server Error",
                "message": f"error executing completion: {ollama_res['error']['message']}",
            }
            status = 500

        print(f" $ ollama response {status}: {ollama_res}")

        return streamify_json(ollama_res, status)

    
    return app



# def start_flask_app(config: ApiConfig):
#     app = Flask(config.FLASK_PROJECT_NAME)
#     app.config["FLASK_RUN_PORT"] = config.FLASK_RUN_PORT
#     app.config["FLASK_DEBUG"] = config.FLASK_DEBUG
#     app.config["FLASK_HOST"] = config.FLASK_HOST
#     app.config["CELERY_BROKER_URL"] = config.CELERY_BROKER_URL
#     app.config["CELERY_BACKEND_URL"] = config.CELERY_BACKEND_URL

#     # celery setup
#     celery_app = create_celery_app_for_flask(app)
#     app.celery = celery_app

#     # endpoints
#     api = Blueprint("api", __name__)
#     api.register_blueprint(generate_blueprint)
#     api.register_blueprint(chat_blueprint)
#     api.register_blueprint(embeddings_blueprint)
#     app.register_blueprint(api)

#     app.run(
#         debug=app.config["FLASK_DEBUG"],
#         host=app.config["FLASK_HOST"],
#         port=app.config["FLASK_RUN_PORT"],
#     )

#     return app
