"""Exercise the existing Oshepherd endpoints through the official Ollama SDK.

uv run python examples/supabase_client.py --client-file /private/client.json
Optional embedding/decision models must already be installed on a worker.
"""

import argparse
import asyncio
import json
from pathlib import Path

import ollama


def check(settings, model, embedding_model=None, decision_model=None):
    kwargs = dict(
        host=settings["gateway_url"],
        headers={"Authorization": "Bearer " + settings["application_key"]},
        timeout=145,
    )
    options = {"num_predict": 16, "num_ctx": 512, "temperature": 0}
    checks = []
    with ollama.Client(**kwargs) as client:
        assert client._request_raw("GET", "/health").json()["status"] == 200
        assert client._request_raw("GET", "/api/version").json()["version"]
        assert any(item.model == model for item in client.list().models)
        # Oshepherd's existing show contract wraps upstream details in model_info.
        assert client.show(model).modelinfo
        assert isinstance(client.ps().models, list)
        checks.extend(["health", "version", "list", "show", "ps"])
        for method, payload in (
            ("generate", {"prompt": "Say hello.", "format": ""}),
            ("chat", {"messages": [{"role": "user", "content": "Say hello."}]}),
        ):
            for stream in (False, True):
                result = getattr(client, method)(
                    model=model, options=options, stream=stream, **payload
                )
                records = list(result) if stream else [result]
                assert records and records[-1].done
                assert records[-1].eval_count > 0
                checks.append(method + ("_stream" if stream else "_json"))
        if embedding_model:
            result = client.embeddings(model=embedding_model, prompt="Hello world")
            assert result.embedding and all(
                isinstance(x, float) for x in result.embedding
            )
            checks.append("embeddings")
        if decision_model:
            result = client.systemone(
                model=decision_model,
                state="The customer was charged twice and requests a refund.",
                questions={
                    "refund": {
                        "type": "noul",
                        "instructions": "Does the customer ask for a refund?",
                    }
                },
            )
            assert result.answers["refund"].type == "noul"
            checks.append("systemone")

    async def asynchronous():
        async with ollama.AsyncClient(**kwargs) as client:
            result = await client.generate(
                model=model, prompt="Hello", format="", options=options
            )
            assert result.done and result.eval_count > 0
            records = [
                item
                async for item in await client.chat(
                    model=model,
                    messages=[{"role": "user", "content": "Hello"}],
                    stream=True,
                    options=options,
                )
            ]
            assert records and records[-1].done and records[-1].eval_count > 0

    asyncio.run(asynchronous())
    checks.extend(["async_generate", "async_chat_stream"])
    return {"passed": True, "checks": checks, "gateway_url": settings["gateway_url"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-file", type=Path, required=True)
    parser.add_argument("--model", default="llama3.2:3b")
    parser.add_argument("--embedding-model")
    parser.add_argument("--decision-model")
    args = parser.parse_args()
    print(
        json.dumps(
            check(
                json.loads(args.client_file.read_text()),
                args.model,
                args.embedding_model,
                args.decision_model,
            ),
            indent=2,
        )
    )
