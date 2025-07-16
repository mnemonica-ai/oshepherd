import os
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
from typing import Optional


def load_and_validate_env(file_path: str, Config: BaseModel) -> Optional[BaseModel]:
    # Load .env file into environment variables
    load_dotenv(file_path)

    # Create a dictionary of environment variables
    env_vars = {
        key: os.getenv(key) for key in Config.__annotations__.keys() if os.getenv(key)
    }

    # Validate environment variables against ApiConfig model
    try:
        config = Config(**env_vars)
    except ValidationError as e:
        print(f"Invalid configuration: {e}")
        return None

    return config
