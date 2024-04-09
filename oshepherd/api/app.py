from flask import Flask, Blueprint
from oshepherd.api.config import ApiConfig
from oshepherd.api.generate.routes import generate_blueprint
from oshepherd.worker.app import create_celery_app_for_flask


def start_flask_app(config: ApiConfig):
    app = Flask(config.FLASK_PROJECT_NAME)
    app.config["FLASK_RUN_PORT"] = config.FLASK_RUN_PORT
    app.config["FLASK_DEBUG"] = config.FLASK_DEBUG
    app.config["FLASK_HOST"] = config.FLASK_HOST
    app.config["RABBITMQ_URL"] = config.RABBITMQ_URL
    app.config["REDIS_URL"] = config.REDIS_URL

    # celery setup
    celery_app = create_celery_app_for_flask(app)
    app.celery = celery_app

    # endpoints
    api = Blueprint("api", __name__)
    api.register_blueprint(generate_blueprint)
    app.register_blueprint(api)

    app.run(
        debug=app.config["FLASK_DEBUG"],
        host=app.config["FLASK_HOST"],
        port=app.config["FLASK_RUN_PORT"],
    )

    return app
