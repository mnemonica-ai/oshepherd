"""Reviewed, receipt-bound hosted removal acceptance on disposable installations.

Default is read-only planning. Execute requires a saved reviewed plan. Disables
the owned cluster, removes only its gateway, checks preservation, and repeats
removal. Optional acknowledgement loss is injected only AFTER an actual provider
delete succeeds, then recovered by real provider reads; it is labelled simulated.
"""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from oshepherd.backends.supabase.edge_deployment import EdgeDeployment
from oshepherd.backends.supabase.management import SupabaseManagement
from oshepherd.backends.supabase.operator import Operator, OperatorConfig, Installation
from oshepherd.backends.supabase.private_files import (
    read_private_json,
    write_private_json,
)
from oshepherd.backends.supabase.releases import Database, OperatorError
from oshepherd.backends.supabase.vercel_deployment import VercelDeployment, VercelTarget


async def run(args):
    config = OperatorConfig.load(args.connection_file)
    state = Installation.load(args.state_dir, config)
    operator = Operator(config, args.state_dir)
    provider = None
    results = {"hosted_qualified": False, "provider": args.provider}
    if args.provider == "edge":
        values = read_private_json(args.provider_file)
        if set(values) != {"organization_id", "access_token"} or not all(
            isinstance(value, str) and value for value in values.values()
        ):
            raise OperatorError("invalid_provider_configuration")
        provider = SupabaseManagement(values["access_token"])
        adapter = EdgeDeployment(
            provider, config, args.state_dir, values["organization_id"]
        )
        identifier_field, expected_field = "function_id", "expected_function_id"
        ambiguous = "provider_outcome_ambiguous"
    else:
        adapter = VercelDeployment(
            VercelTarget.load(args.provider_file), config, args.state_dir
        )
        identifier_field, expected_field = "deployment_id", "expected_deployment_id"
        ambiguous = "vercel_removal_outcome_ambiguous"
    try:
        plan = await adapter.removal_plan()
        results["plan"] = plan
        if not args.execute:
            write_private_json(args.plan_file, plan)
            return results
        reviewed = read_private_json(args.plan_file)
        # Presence may change after a lost delete acknowledgement. Ownership,
        # resource ID, cluster and preservation scope must still match exactly.
        fields = set(plan) | set(reviewed)
        fields.discard("deployment_present")
        if any(reviewed.get(name) != plan.get(name) for name in fields):
            raise OperatorError("reviewed_removal_plan_changed")
        cluster = str(UUID(str(state.cluster_id)))
        database = Database(config)

        def snapshot():
            return json.loads(
                database.execute(
                    "select json_build_object('job_count',count(*),'active_jobs',count(*) filter "
                    "(where state in ('queued','running')),'job_ids_digest',md5(coalesce(string_agg(id::text,',' order by id),'')),"
                    "'schema_fingerprint',oshepherd.schema_fingerprint(),"
                    "'shared_schedules',(select json_agg(json_build_object('name',jobname,'command',command) order by jobname) "
                    "from cron.job where jobname in ('oshepherd-reap-v1','oshepherd-purge-v1'))) "
                    f"from oshepherd.jobs where cluster_id='{cluster}';"
                )
            )

        before = await asyncio.to_thread(snapshot)
        if before["active_jobs"]:
            raise OperatorError("removal_requires_idle_disposable_installation")
        results["before"] = before
        if args.simulate_ack_loss:
            if provider:
                original = provider.request

                async def edge_request(method, path, **kwargs):
                    value = await original(method, path, **kwargs)
                    if method == "DELETE" and not results.get("delete_ack_discarded"):
                        results["delete_ack_discarded"] = True
                        raise OperatorError(ambiguous)
                    return value

                provider.request = edge_request
            else:
                original = adapter.request

                async def vercel_request(path, *, method="GET", **params):
                    value = await original(path, method=method, **params)
                    if method == "DELETE" and not results.get("delete_ack_discarded"):
                        results["delete_ack_discarded"] = True
                        raise OperatorError(ambiguous)
                    return value

                adapter.request = vercel_request
            try:
                await adapter.remove(
                    **{expected_field: plan[identifier_field]}, operator=operator
                )
            except OperatorError as error:
                if str(error) != ambiguous:
                    raise
                results["simulated_ack_loss_observed"] = True
            else:
                # Retry may find an already removed resource; no second delete
                # or fabricated acknowledgement-loss evidence is required.
                results["simulated_ack_loss_observed"] = False
        results["removed"] = await adapter.remove(
            **{expected_field: plan[identifier_field]}, operator=operator
        )
        results["repeat"] = await adapter.remove(
            **{expected_field: plan[identifier_field]}, operator=operator
        )
        after = await asyncio.to_thread(snapshot)
        results["after"] = after
        assert before == after, "retained_data_or_shared_schema_schedule_changed"
        doctor = await operator.doctor()
        assert not doctor["enabled"] and doctor["active_jobs"] == 0
        results["disabled"] = True
        results["removal_acceptance_passed"] = True
        return results
    finally:
        await adapter.close()
        if provider:
            await provider.close()
        await operator.close()
        write_private_json(args.report_file, results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("edge", "vercel"), required=True)
    for name in (
        "connection-file",
        "state-dir",
        "provider-file",
        "plan-file",
        "report-file",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--simulate-ack-loss", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args))))
    except OperatorError as error:
        raise SystemExit(str(error)) from None
    except (ValueError, OSError, KeyError, TypeError):
        raise SystemExit(
            "Invalid private removal configuration; no secrets printed"
        ) from None
