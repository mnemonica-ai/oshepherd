import json

import pytest
from jsonschema import Draft202012Validator

from oshepherd.api.chat.models import ChatRequestPayload
from oshepherd.api.embeddings.models import EmbeddingsPayload
from oshepherd.api.generate.models import GenerateRequestPayload
from oshepherd.api.show.models import ShowRequest
from oshepherd.api.systemone.models import SystemOneRequestPayload
from tests.contracts.conftest import CASES, PROTOCOL

MODELS = {
    "generate": GenerateRequestPayload,
    "chat": ChatRequestPayload,
    "embeddings": EmbeddingsPayload,
    "systemone": SystemOneRequestPayload,
    "show": ShowRequest,
}


@pytest.mark.parametrize("operation", MODELS)
def test_checked_in_schema_matches_current_model(operation):
    schema = json.loads(
        (PROTOCOL / "schemas" / f"{operation}.request.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    for extension in ("$schema", "$id", "x-extra-behavior"):
        schema.pop(extension)
    assert schema == MODELS[operation].model_json_schema()


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if "normalized" in case],
    ids=lambda case: case["id"],
)
def test_normalized_payload_matches_language_neutral_schema(case):
    operation = case["normalized"]["type"]
    schema = json.loads(
        (PROTOCOL / "schemas" / f"{operation}.request.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(case["normalized"]["payload"])
    model = MODELS[operation].model_validate(case["request"]["json"])
    assert model.model_dump(mode="json") == case["normalized"]["payload"]


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in CASES
        if case["id"]
        in (
            "generate-missing-prompt",
            "generate-format-object",
            "chat-tool-role",
            "chat-format-object",
            "embeddings-prompt-only",
            "show-missing-model",
            "systemone-missing",
            "systemone-invalid-questions",
        )
    ],
    ids=lambda case: case["id"],
)
def test_rejected_payload_schema(case):
    operation = case["request"]["path"].rsplit("/", 1)[-1]
    schema = json.loads(
        (PROTOCOL / "schemas" / f"{operation}.request.schema.json").read_text()
    )
    assert list(Draft202012Validator(schema).iter_errors(case["request"]["json"]))
