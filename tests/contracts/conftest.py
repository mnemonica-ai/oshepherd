import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from oshepherd.api.app import setup_api_app
from oshepherd.api.config import ApiConfig

PROTOCOL = Path(__file__).resolve().parents[2] / "protocol" / "v1"
CASES = json.loads((PROTOCOL / "fixtures" / "routes.json").read_text())["cases"]


@pytest.fixture
def legacy_api(monkeypatch):
    # Refuse actual Redis construction. Exercise the production registry methods
    # against wire-format hashes, including live/offline/missing heartbeats.
    store = MagicMock()
    monkeypatch.setattr("oshepherd.api.network_data.RedisService", lambda _: store)

    class FixtureClock:
        fromisoformat = datetime.fromisoformat

        @staticmethod
        def now(tz):
            return datetime(2026, 10, 4, 12, tzinfo=timezone.utc)

    monkeypatch.setattr("oshepherd.api.network_data.datetime", FixtureClock)
    app = setup_api_app(
        ApiConfig(
            CELERY_BROKER_URL="memory://", CELERY_BACKEND_URL="redis://localhost:6379/0"
        )
    )
    from oshepherd.worker.tasks import exec_completion

    task = MagicMock(id="fixture-task")
    task.ready.return_value = True
    delay = MagicMock(return_value=task)
    monkeypatch.setattr(exec_completion, "delay", delay)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(
            client=client, app=app, store=store, task=task, delay=delay
        )


def set_workers(api, workers):
    api.store.scan_iter.return_value = [f"worker:{i}" for i in range(len(workers))]
    api.store.hgetall.side_effect = [
        {key.encode(): value.encode() for key, value in worker.items()}
        for worker in workers
    ]
