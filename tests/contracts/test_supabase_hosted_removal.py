"""Removal harness never mutates an unreviewed, changed or active installation."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

import scripts.supabase_hosted_removal as probe
from oshepherd.backends.supabase.private_files import (
    read_private_json,
    write_private_json,
)
from oshepherd.backends.supabase.releases import OperatorError


def setup(tmp_path, monkeypatch, *, execute=False, active=0):
    plan = {
        "project_ref": "a" * 20,
        "function_id": str(uuid4()),
        "slug": "ollama",
        "disable_cluster_first": str(uuid4()),
        "preserve": ["database"],
        "destructive": True,
    }
    args = SimpleNamespace(
        provider="edge",
        connection_file="unused",
        state_dir=tmp_path,
        provider_file=tmp_path / "provider.json",
        plan_file=tmp_path / "plan.json",
        report_file=tmp_path / "report.json",
        execute=execute,
        simulate_ack_loss=False,
    )
    write_private_json(
        args.provider_file, {"organization_id": "org", "access_token": "hidden"}
    )
    write_private_json(args.plan_file, plan)
    monkeypatch.setattr(probe.OperatorConfig, "load", lambda _: SimpleNamespace())
    monkeypatch.setattr(
        probe.Installation,
        "load",
        lambda *_: SimpleNamespace(cluster_id=plan["disable_cluster_first"]),
    )

    class Operator:
        def __init__(self, *_):
            pass

        async def close(self):
            pass

        async def disable(self):
            pytest.fail("unreviewed cluster disable")

    class Provider:
        def __init__(self, *_):
            pass

        async def close(self):
            pass

    class Adapter:
        def __init__(self, *_):
            pass

        async def close(self):
            pass

        async def removal_plan(self):
            return dict(plan)

        async def remove(self, **_):
            pytest.fail("unreviewed provider deletion")

    class Database:
        def __init__(self, _):
            pass

        def execute(self, _):
            if not execute:
                pytest.fail("default planning must not inspect/mutate SQL")
            return json.dumps({"active_jobs": active})

    monkeypatch.setattr(probe, "Operator", Operator)
    monkeypatch.setattr(probe, "SupabaseManagement", Provider)
    monkeypatch.setattr(probe, "EdgeDeployment", Adapter)
    monkeypatch.setattr(probe, "Database", Database)
    return args, plan


def test_default_only_writes_read_only_plan(tmp_path, monkeypatch):
    args, plan = setup(tmp_path, monkeypatch)
    assert asyncio.run(probe.run(args))["plan"] == plan
    assert read_private_json(args.plan_file) == plan
    assert "removal_acceptance_passed" not in read_private_json(args.report_file)


@pytest.mark.parametrize(
    "field", ["project_ref", "function_id", "disable_cluster_first", "preserve"]
)
def test_stale_or_changed_reviewed_target_never_disables_or_deletes(
    tmp_path, monkeypatch, field
):
    args, plan = setup(tmp_path, monkeypatch, execute=True)
    reviewed = dict(plan)
    reviewed[field] = ["changed"] if field == "preserve" else "changed"
    write_private_json(args.plan_file, reviewed)
    with pytest.raises(OperatorError, match="reviewed_removal_plan_changed"):
        asyncio.run(probe.run(args))


def test_active_installation_is_refused_before_disable_or_delete(tmp_path, monkeypatch):
    args, _ = setup(tmp_path, monkeypatch, execute=True, active=1)
    with pytest.raises(
        OperatorError, match="removal_requires_idle_disposable_installation"
    ):
        asyncio.run(probe.run(args))
