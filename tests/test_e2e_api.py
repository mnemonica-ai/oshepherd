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

HOST = "http://127.0.0.1:5001"
GENERATE_ENDPOINT = f"{HOST}/api/generate/"
CHAT_ENDPOINT = f"{HOST}/api/chat/"
EMBEDDINGS_ENDPOINT = f"{HOST}/api/embeddings/"

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

    ollama_res = GenerateResponse(**ollama_res)
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

    ollama_res = ChatResponse(**ollama_res)
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

    ollama_res = EmbeddingsResponse(**ollama_res)
    assert len(ollama_res.embedding) > 0, "response should not be empty"
