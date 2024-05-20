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


def test_basic_generate_completion_using_ollama():
    params = {"model": "mistral", "prompt": "Why is the sky blue?"}
    client = ollama.Client(host="http://127.0.0.1:5001")
    ollama_res = client.generate(**params)

    ollama_res = GenerateResponse(**ollama_res)
    assert ollama_res.response, "response should not be empty"


def test_basic_generate_completion_using_requests():
    url = "http://127.0.0.1:5001/api/generate/"
    headers = {"Content-Type": "application/json"}
    data = {"model": "mistral", "prompt": "Why is the sky blue?"}
    response = requests.post(url, headers=headers, data=json.dumps(data))

    assert response.status_code == 200
    assert "error" not in response
    ollama_res = GenerateResponse(**response.json())
    assert ollama_res.response, "response should not be empty"


def test_basic_chat_completion_using_ollama():
    params = {
        "model": "mistral",
        "messages": [{"role": "user", "content": "why is the sky blue?"}],
    }
    client = ollama.Client(host="http://127.0.0.1:5001")
    ollama_res = client.chat(**params)

    ollama_res = ChatResponse(**ollama_res)
    assert ollama_res.message.content, "response should not be empty"


def test_basic_chat_completion_using_requests():
    url = "http://127.0.0.1:5001/api/chat/"
    headers = {"Content-Type": "application/json"}
    data = {
        "model": "mistral",
        "messages": [{"role": "user", "content": "why is the sky blue?"}],
    }
    response = requests.post(url, headers=headers, data=json.dumps(data))

    assert response.status_code == 200
    assert "error" not in response
    ollama_res = ChatResponse(**response.json())
    assert ollama_res.message.content, "response should not be empty"


def test_basic_embeddings_using_requests():
    url = "http://127.0.0.1:5001/api/embeddings/"
    headers = {"Content-Type": "application/json"}
    data = {
        "model": "mistral",
        "prompt": "The sky is blue because of rayleigh scattering",
    }
    response = requests.post(url, headers=headers, data=json.dumps(data))

    assert response.status_code == 200
    assert "error" not in response
    ollama_res = EmbeddingsResponse(**response.json())
    assert len(ollama_res.embedding) > 0, "response should not be empty"


def test_basic_embeddings_using_ollama():
    params = {
        "model": "mistral",
        "prompt": "The sky is blue because of rayleigh scattering",
    }
    client = ollama.Client(host="http://127.0.0.1:5001")
    ollama_res = client.embeddings(**params)

    ollama_res = EmbeddingsResponse(**ollama_res)
    assert len(ollama_res.embedding) > 0, "response should not be empty"


def test_basic_tags_using_requests():
    url = "http://127.0.0.1:5001/api/tags/"
    headers = {"Content-Type": "application/json"}
    response = requests.get(url, headers=headers)

    assert response.status_code == 200
    assert "error" not in response

    ollama_res = TagsResponse(**response.json())

    assert isinstance(ollama_res.models, list), "response should be a list"
    assert len(ollama_res.models) > 0, "response list should not be empty"


def test_basic_tags_using_ollama():
    client = ollama.Client(host="http://127.0.0.1:5001")
    ollama_res = client.list()

    ollama_res = TagsResponse(**response.json())

    assert isinstance(ollama_res.models, list), "response should be a list"
    assert len(ollama_res.models) > 0, "response list should not be empty"
