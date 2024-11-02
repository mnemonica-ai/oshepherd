import json
from fastapi.responses import StreamingResponse


def streamify_json(json_data, status=200):
    async def json_stream(data):
        yield json.dumps(data).encode("utf-8")

    return StreamingResponse(
        json_stream(json_data),
        status_code=status,
        media_type="application/json",
    )
