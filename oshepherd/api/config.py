from pydantic import BaseModel
from typing import Optional


class ApiConfig(BaseModel):
    FLASK_PROJECT_NAME: Optional[str] = "oshepherd_api"
    FLASK_ENV: Optional[str] = "development"
    FLASK_DEBUG: Optional[bool] = True
    FLASK_RUN_PORT: Optional[int] = 5001
    FLASK_HOST: Optional[str] = "0.0.0.0"
    RABBITMQ_URL: str
    REDIS_URL: str
