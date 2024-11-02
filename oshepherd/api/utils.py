# import json
# from flask import Response, stream_with_context


# def streamify_json(json_data, status=200):
#     def json_stream(data):
#         yield json.dumps(data)

#     return Response(
#         stream_with_context(json_stream(json_data)),
#         status=status,
#         mimetype="application/json",
#     )

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
