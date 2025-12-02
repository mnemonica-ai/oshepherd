"""
Basic end to end tests, using Ollama python package, and its equivalent http requests to an Ollama server in local.
i.e.:
    curl -X POST -H "Content-Type: application/json" -L http://127.0.0.1:11434/api/generate/ -d '{
        "model": "mistral",
        "prompt":"Why is the sky blue?"
    }'
"""

import json
import requests
import ollama
from oshepherd.api.generate.models import GenerateResponse
from oshepherd.api.chat.models import ChatResponse
from oshepherd.api.embeddings.models import EmbeddingsResponse
from oshepherd.api.tags.models import TagsResponse
from oshepherd.api.version.models import VersionResponse
from oshepherd.common.ollama import serialize_ollama_res

HOST = "http://127.0.0.1:5001"
GENERATE_ENDPOINT = f"{HOST}/api/generate/"
CHAT_ENDPOINT = f"{HOST}/api/chat/"
EMBEDDINGS_ENDPOINT = f"{HOST}/api/embeddings/"
TAGS_ENDPOINT = f"{HOST}/api/tags/"
VERSION_ENDPOINT = f"{HOST}/api/version/"
SHOW_ENDPOINT = f"{HOST}/api/show/"
PS_ENDPOINT = f"{HOST}/api/ps/"

req_headers = {"Content-Type": "application/json"}


def test_health_endpoint():
    response = requests.get(f"{HOST}/health", headers=req_headers)

    assert response.status_code == 200
    assert "error" not in response
    res_json = response.json()
    assert res_json == {'status': 200}, "response should not be empty"


def test_basic_generate_completion_using_ollama():
    params = {"model": "mistral", "prompt": "Why is the sky blue?"}
    client = ollama.Client(host=HOST)
    ollama_res = client.generate(**params)

    ollama_dict = serialize_ollama_res(ollama_res)
    ollama_res = GenerateResponse(**ollama_dict)
    assert ollama_res.response, "response should not be empty"


def test_basic_generate_completion_using_requests():
    data = {"model": "mistral", "prompt": "Why is the sky blue?"}
    response = requests.post(GENERATE_ENDPOINT, headers=req_headers, data=json.dumps(data))

    assert response.status_code == 200
    assert "error" not in response
    ollama_res = GenerateResponse(**response.json())
    assert ollama_res.response, "response should not be empty"


def test_basic_chat_completion_using_ollama():
    params = {
        "model": "mistral",
        "messages": [{"role": "user", "content": "why is the sky blue?"}],
    }
    client = ollama.Client(host=HOST)
    ollama_res = client.chat(**params)

    ollama_dict = serialize_ollama_res(ollama_res)
    ollama_res = ChatResponse(**ollama_dict)
    assert ollama_res.message.content, "response should not be empty"


def test_basic_chat_completion_using_requests():
    data = {
        "model": "mistral",
        "messages": [{"role": "user", "content": "why is the sky blue?"}],
    }
    response = requests.post(CHAT_ENDPOINT, headers=req_headers, data=json.dumps(data))

    assert response.status_code == 200
    assert "error" not in response
    ollama_res = ChatResponse(**response.json())
    assert ollama_res.message.content, "response should not be empty"


def test_basic_embeddings_using_requests():
    data = {
        "model": "mistral",
        "prompt": "The sky is blue because of rayleigh scattering",
    }
    response = requests.post(EMBEDDINGS_ENDPOINT, headers=req_headers, data=json.dumps(data))

    assert response.status_code == 200
    assert "error" not in response
    ollama_res = EmbeddingsResponse(**response.json())
    assert len(ollama_res.embedding) > 0, "response should not be empty"


def test_basic_embeddings_using_ollama():
    params = {
        "model": "mistral",
        "prompt": "The sky is blue because of rayleigh scattering",
    }
    client = ollama.Client(host=HOST)
    ollama_res = client.embeddings(**params)

    ollama_dict = serialize_ollama_res(ollama_res)
    ollama_res = EmbeddingsResponse(**ollama_dict)
    assert len(ollama_res.embedding) > 0, "response should not be empty"


def test_basic_tags_using_requests():
    response = requests.get(TAGS_ENDPOINT, headers=req_headers)

    assert response.status_code == 200
    assert "error" not in response
    ollama_res = TagsResponse(**response.json())
    assert len(ollama_res.models) > 0, "response should not be empty"


def test_basic_tags_using_ollama():
    client = ollama.Client(host=HOST)
    ollama_res = client.list()

    ollama_dict = serialize_ollama_res(ollama_res)
    ollama_res = TagsResponse(**ollama_dict)
    assert len(ollama_res.models) > 0, "response should not be empty"


def test_basic_version_using_requests():
    response = requests.get(VERSION_ENDPOINT, headers=req_headers)

    assert response.status_code == 200
    assert "error" not in response
    ollama_res = VersionResponse(**response.json())
    assert ollama_res.version, "version should not be an empty string"


def test_basic_show_using_requests():
    data = {"model": "mistral:latest"}
    response = requests.post(SHOW_ENDPOINT, headers=req_headers, data=json.dumps(data))

    assert response.status_code == 200
    res_json = response.json()
    assert "error" not in res_json
    # Check for expected fields in show response (nested under model_info)
    model_info = res_json.get("model_info", {})
    assert "modelfile" in model_info or "license" in model_info or "parameters" in model_info, "response should contain model information"


def test_basic_show_using_ollama():
    client = ollama.Client(host=HOST)
    ollama_res = client.show("mistral:latest")

    ollama_dict = serialize_ollama_res(ollama_res)
    # Check for expected fields in show response
    assert "modelfile" in ollama_dict or "license" in ollama_dict or "parameters" in ollama_dict, "response should contain model information"


def test_basic_ps_using_requests():
    response = requests.get(PS_ENDPOINT, headers=req_headers)

    assert response.status_code == 200
    res_json = response.json()
    assert "error" not in res_json
    assert "models" in res_json, "response should contain models array"
    # Running models list can be empty if no models are currently loaded
    assert isinstance(res_json["models"], list), "models should be a list"


def test_basic_ps_using_ollama():
    client = ollama.Client(host=HOST)
    ollama_res = client.ps()

    ollama_dict = serialize_ollama_res(ollama_res)
    assert "models" in ollama_dict, "response should contain models array"
    # Running models list can be empty if no models are currently loaded
    assert isinstance(ollama_dict["models"], list), "models should be a list"
