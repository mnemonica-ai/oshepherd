import json
import os
from pathlib import Path

import pytest


class LocalConnection(dict):
    def __repr__(self):
        return (
            f"LocalConnection(API_URL={self.get('API_URL')!r}, credentials=[redacted])"
        )


@pytest.fixture(scope="session")
def local_connection():
    filename = Path(
        os.environ.get("OSHEPHERD_LOCAL_CONNECTION", ".oshepherd/local/connection.json")
    )
    if not filename.is_file():
        pytest.fail(
            "Local Supabase connection state missing; start the isolated stack before running this suite"
        )
    data = json.loads(filename.read_text())
    if data.get("API_URL") != "http://127.0.0.1:55321":
        pytest.fail(
            "Integration suite accepts only the isolated local development origin"
        )
    return LocalConnection(data)


@pytest.fixture(scope="session")
def production_schema(local_connection):
    from types import SimpleNamespace
    from pydantic import SecretStr
    from oshepherd.backends.supabase.releases import Database

    config = SimpleNamespace(
        project_ref="local",
        database_url=SecretStr(
            "postgresql://supabase_admin:postgres@127.0.0.1:55322/postgres"
        ),
    )
    Database(config).migrate()
