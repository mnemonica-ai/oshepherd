from typing import Any, Union


def serialize_ollama_res(response: Any) -> Union[dict, Any]:
    """
    Convert response objects to dict for JSON serialization.

    Args:
        response: The response object to serialize

    Returns:
        dict: Serializable dictionary representation of the response
    """
    if hasattr(response, "model_dump"):
        return response.model_dump()
    elif hasattr(response, "__dict__"):
        return dict(response)
    else:
        return response
