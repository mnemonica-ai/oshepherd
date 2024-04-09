"""
Basic end to end tests, using an equivalent request as pointing to ollama api server in local:
    curl -X POST -H "Content-Type: application/json" -L http://127.0.0.1:11434/api/generate/ -d '{
        "model": "mistral",
        "prompt":"Why is the sky blue?"
    }'
"""

import json
import requests
import ollama
from oshepherd.api.generate.models import GenerateResponse


def test_basic_api_worker_queueing_using_ollama():
    params = {"model": "mistral", "prompt": "Why is the sky blue?"}
    client = ollama.Client(host="http://127.0.0.1:5001")
    ollama_res = client.generate(**params)

    ollama_res = GenerateResponse(**ollama_res)
    assert ollama_res.response, "response should not be empty"


def test_basic_api_worker_queueing_using_requests():
    url = "http://127.0.0.1:5001/api/generate/"
    headers = {"Content-Type": "application/json"}
    data = {"model": "mistral", "prompt": "Why is the sky blue?"}
    response = requests.post(url, headers=headers, data=json.dumps(data))

    assert response.status_code == 200
    assert "error" not in response
    ollama_res = GenerateResponse(**response.json())
    assert ollama_res.response, "response should not be empty"
