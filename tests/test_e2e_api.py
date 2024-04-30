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
