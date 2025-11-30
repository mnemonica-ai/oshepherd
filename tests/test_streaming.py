"""
End-to-end tests for streaming endpoints.

Tests streaming functionality for /api/generate and /api/chat endpoints
using both the Ollama Python client and raw HTTP requests.
"""

import json
import requests
import ollama
from oshepherd.common.ollama import serialize_ollama_res

HOST = "http://127.0.0.1:5001"
GENERATE_ENDPOINT = f"{HOST}/api/generate/"
CHAT_ENDPOINT = f"{HOST}/api/chat/"

req_headers = {"Content-Type": "application/json"}


def test_streaming_generate_completion_using_ollama():
    """Test streaming generate completion using ollama-python client."""
    params = {
        "model": "mistral",
        "prompt": "Why is the sky blue?",
        "stream": True,
    }
    client = ollama.Client(host=HOST)

    chunks_received = 0
    full_response = ""
    final_chunk = None

    for chunk in client.generate(**params):
        chunks_received += 1
        chunk_dict = serialize_ollama_res(chunk)

        # Verify chunk structure
        assert "response" in chunk_dict, "Each chunk should have 'response' field"
        assert "done" in chunk_dict, "Each chunk should have 'done' field"

        # Accumulate response
        full_response += chunk_dict["response"]

        # Store the final chunk
        if chunk_dict["done"]:
            final_chunk = chunk_dict

    # Assertions
    assert chunks_received > 1, "Should receive multiple chunks for streaming"
    assert final_chunk is not None, "Should receive final chunk with done=True"
    assert final_chunk["done"] is True, "Final chunk should have done=True"
    assert full_response, "Accumulated response should not be empty"
    assert len(full_response) > 0, "Response should contain text"

    print(f"✓ Received {chunks_received} chunks")
    print(f"✓ Full response length: {len(full_response)} characters")


def test_streaming_generate_completion_using_requests():
    """Test streaming generate completion using raw HTTP requests."""
    data = {
        "model": "mistral",
        "prompt": "Why is the sky blue?",
        "stream": True,
    }
    response = requests.post(
        GENERATE_ENDPOINT,
        headers=req_headers,
        data=json.dumps(data),
        stream=True,
    )

    assert response.status_code == 200, "Response should be successful"
    assert (
        response.headers.get("content-type") == "application/x-ndjson"
    ), "Content-Type should be application/x-ndjson for streaming"

    chunks_received = 0
    full_response = ""
    final_chunk = None

    # Parse NDJSON stream
    for line in response.iter_lines(decode_unicode=True):
        if line:
            chunks_received += 1
            chunk = json.loads(line)

            # Verify chunk structure
            assert "response" in chunk, "Each chunk should have 'response' field"
            assert "done" in chunk, "Each chunk should have 'done' field"

            # Accumulate response
            full_response += chunk["response"]

            # Store the final chunk
            if chunk["done"]:
                final_chunk = chunk

    # Assertions
    assert chunks_received > 1, "Should receive multiple chunks for streaming"
    assert final_chunk is not None, "Should receive final chunk with done=True"
    assert final_chunk["done"] is True, "Final chunk should have done=True"
    assert full_response, "Accumulated response should not be empty"
    assert len(full_response) > 0, "Response should contain text"

    print(f"✓ Received {chunks_received} chunks")
    print(f"✓ Full response length: {len(full_response)} characters")


def test_streaming_chat_completion_using_ollama():
    """Test streaming chat completion using ollama-python client."""
    params = {
        "model": "mistral",
        "messages": [{"role": "user", "content": "why is the sky blue?"}],
        "stream": True,
    }
    client = ollama.Client(host=HOST)

    chunks_received = 0
    full_response = ""
    final_chunk = None

    for chunk in client.chat(**params):
        chunks_received += 1
        chunk_dict = serialize_ollama_res(chunk)

        # Verify chunk structure
        assert "message" in chunk_dict, "Each chunk should have 'message' field"
        assert "done" in chunk_dict, "Each chunk should have 'done' field"

        # Accumulate response
        if "content" in chunk_dict["message"]:
            full_response += chunk_dict["message"]["content"]

        # Store the final chunk
        if chunk_dict["done"]:
            final_chunk = chunk_dict

    # Assertions
    assert chunks_received > 1, "Should receive multiple chunks for streaming"
    assert final_chunk is not None, "Should receive final chunk with done=True"
    assert final_chunk["done"] is True, "Final chunk should have done=True"
    assert full_response, "Accumulated response should not be empty"
    assert len(full_response) > 0, "Response should contain text"

    print(f"✓ Received {chunks_received} chunks")
    print(f"✓ Full response length: {len(full_response)} characters")


def test_streaming_chat_completion_using_requests():
    """Test streaming chat completion using raw HTTP requests."""
    data = {
        "model": "mistral",
        "messages": [{"role": "user", "content": "why is the sky blue?"}],
        "stream": True,
    }
    response = requests.post(
        CHAT_ENDPOINT,
        headers=req_headers,
        data=json.dumps(data),
        stream=True,
    )

    assert response.status_code == 200, "Response should be successful"
    assert (
        response.headers.get("content-type") == "application/x-ndjson"
    ), "Content-Type should be application/x-ndjson for streaming"

    chunks_received = 0
    full_response = ""
    final_chunk = None

    # Parse NDJSON stream
    for line in response.iter_lines(decode_unicode=True):
        if line:
            chunks_received += 1
            chunk = json.loads(line)

            # Verify chunk structure
            assert "message" in chunk, "Each chunk should have 'message' field"
            assert "done" in chunk, "Each chunk should have 'done' field"

            # Accumulate response
            if "content" in chunk["message"]:
                full_response += chunk["message"]["content"]

            # Store the final chunk
            if chunk["done"]:
                final_chunk = chunk

    # Assertions
    assert chunks_received > 1, "Should receive multiple chunks for streaming"
    assert final_chunk is not None, "Should receive final chunk with done=True"
    assert final_chunk["done"] is True, "Final chunk should have done=True"
    assert full_response, "Accumulated response should not be empty"
    assert len(full_response) > 0, "Response should contain text"

    print(f"✓ Received {chunks_received} chunks")
    print(f"✓ Full response length: {len(full_response)} characters")


def test_non_streaming_still_works_generate():
    """Verify that non-streaming mode still works for generate endpoint."""
    data = {
        "model": "mistral",
        "prompt": "Why is the sky blue?",
        "stream": False,
    }
    response = requests.post(GENERATE_ENDPOINT, headers=req_headers, data=json.dumps(data))

    assert response.status_code == 200, "Response should be successful"
    assert (
        response.headers.get("content-type") == "application/json"
    ), "Content-Type should be application/json for non-streaming"

    res_json = response.json()
    assert "response" in res_json, "Response should have 'response' field"
    assert res_json["response"], "Response should not be empty"
    assert res_json.get("done") is True, "Response should be complete"

    print(f"✓ Non-streaming response length: {len(res_json['response'])} characters")


def test_non_streaming_still_works_chat():
    """Verify that non-streaming mode still works for chat endpoint."""
    data = {
        "model": "mistral",
        "messages": [{"role": "user", "content": "why is the sky blue?"}],
        "stream": False,
    }
    response = requests.post(CHAT_ENDPOINT, headers=req_headers, data=json.dumps(data))

    assert response.status_code == 200, "Response should be successful"
    assert (
        response.headers.get("content-type") == "application/json"
    ), "Content-Type should be application/json for non-streaming"

    res_json = response.json()
    assert "message" in res_json, "Response should have 'message' field"
    assert "content" in res_json["message"], "Message should have 'content' field"
    assert res_json["message"]["content"], "Response content should not be empty"
    assert res_json.get("done") is True, "Response should be complete"

    print(
        f"✓ Non-streaming response length: {len(res_json['message']['content'])} characters"
    )


def test_default_no_stream_parameter_generate():
    """Verify that omitting stream parameter defaults to non-streaming (generate)."""
    data = {"model": "mistral", "prompt": "Why is the sky blue?"}
    response = requests.post(GENERATE_ENDPOINT, headers=req_headers, data=json.dumps(data))

    assert response.status_code == 200, "Response should be successful"
    assert (
        response.headers.get("content-type") == "application/json"
    ), "Content-Type should be application/json when stream not specified"

    res_json = response.json()
    assert "response" in res_json, "Response should have 'response' field"
    assert res_json["response"], "Response should not be empty"

    print("✓ Default behavior (no stream param) uses non-streaming mode")


def test_default_no_stream_parameter_chat():
    """Verify that omitting stream parameter defaults to non-streaming (chat)."""
    data = {
        "model": "mistral",
        "messages": [{"role": "user", "content": "why is the sky blue?"}],
    }
    response = requests.post(CHAT_ENDPOINT, headers=req_headers, data=json.dumps(data))

    assert response.status_code == 200, "Response should be successful"
    assert (
        response.headers.get("content-type") == "application/json"
    ), "Content-Type should be application/json when stream not specified"

    res_json = response.json()
    assert "message" in res_json, "Response should have 'message' field"
    assert res_json["message"]["content"], "Response content should not be empty"

    print("✓ Default behavior (no stream param) uses non-streaming mode")
