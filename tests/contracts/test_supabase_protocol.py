import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from oshepherd.backends.supabase.client import RPCS

ROOT = Path(__file__).resolve().parents[2] / "protocol" / "supabase" / "v1"


@pytest.mark.parametrize("filename", ["rpc.schema.json", "async.schema.json"])
def test_wire_schemas_are_valid(filename):
    Draft202012Validator.check_schema(json.loads((ROOT / filename).read_text()))


def test_every_transport_operation_has_a_wire_request_schema():
    schema = json.loads((ROOT / "rpc.schema.json").read_text())
    assert set(schema["$defs"]) == RPCS | {"response"}
    for name in RPCS:
        assert schema["$defs"][name]["additionalProperties"] is False
        assert {"v", "operation_id"} <= set(schema["$defs"][name]["required"])


def test_all_async_states_and_content_expiry_have_fixtures():
    schema = json.loads((ROOT / "async.schema.json").read_text())
    fixtures = json.loads((ROOT / "async-status.fixtures.json").read_text())
    assert {item["status"] for item in fixtures["states"]} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "expired",
    }
    validator = Draft202012Validator(
        schema["$defs"]["status"], format_checker=FormatChecker()
    )
    for fixture in [*fixtures["states"], fixtures["content_expired"]]:
        validator.validate(fixture)
    assert fixtures["content_expired"]["status"] == "succeeded"
    assert fixtures["content_expired"]["result"]["available"] is False
