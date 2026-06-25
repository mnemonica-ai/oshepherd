import os
import logging
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
from typing import Optional

logger = logging.getLogger(__name__)


def load_and_validate_env(
    Config: BaseModel, file_path: Optional[str] = None
) -> Optional[BaseModel]:
    if file_path:
        load_dotenv(file_path)
    else:
        load_dotenv()

    # Create a env var dictionary
    env_vars = {
        key: os.getenv(key) for key in Config.__annotations__.keys() if os.getenv(key)
    }

    # Validate env vars against ApiConfig model
    try:
        config = Config(**env_vars)
    except ValidationError as e:
        logger.error("invalid configuration error=%s", e)
        return None

    return config
