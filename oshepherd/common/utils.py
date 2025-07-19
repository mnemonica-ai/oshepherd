import os
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError
from typing import Optional


def load_and_validate_env(file_path: str, Config: BaseModel) -> Optional[BaseModel]:
    # Load .env file into environment variables
    if file_path:
        print(f" > Loading environment from: {file_path}")
        load_dotenv(file_path)
    else:
        print(" > No environment file specified, using system environment and defaults")

    # Create a dictionary of environment variables, including all values
    env_vars = {}
    for key in Config.__annotations__.keys():
        value = os.getenv(key)
        if value is not None:  # Include all non-None values, including empty strings
            env_vars[key] = value

    print(f" > Loaded environment variables: {list(env_vars.keys())}")
    print(f" > Environment values: {env_vars}")

    # Validate environment variables against Config model
    try:
        config = Config(**env_vars)
        print(f" > Configuration validated successfully")
        return config
    except ValidationError as e:
        print(f"Invalid configuration: {e}")
        return None
