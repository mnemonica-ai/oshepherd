from typing import Any, Union


def serialize_ollama_res(response: Any) -> Union[dict, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump()
    elif hasattr(response, "__dict__"):
        return dict(response)
    else:
        return response
