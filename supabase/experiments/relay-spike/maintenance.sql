-- Experimental bounded cleanup. Storage bytes are deleted only through its
-- HTTP API; SQL records durable intent and verifies the resulting metadata.
begin;
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_spike_schema_v1',0));
set local role oshepherd_spike_runtime;
create table if not exists oshepherd_spike.maintenance_cursors (
  cluster_id uuid primary key references oshepherd_spike.clusters(id) on delete cascade,
  object_cursor text not null default ''
);
create table if not exists oshepherd_spike.maintenance_operations (
  operation_id uuid primary key,
  cluster_id uuid not null references oshepherd_spike.clusters(id) on delete cascade,
  kind text not null,
  request_hash bytea not null,
  response jsonb not null,
  expires_at timestamptz not null default pg_catalog.clock_timestamp()+interval '2 days'
);
create index if not exists maintenance_operations_age on oshepherd_spike.maintenance_operations(cluster_id,expires_at,operation_id);
alter table oshepherd_spike.maintenance_cursors enable row level security;
alter table oshepherd_spike.maintenance_operations enable row level security;
revoke all on oshepherd_spike.maintenance_cursors,oshepherd_spike.maintenance_operations from public,anon,authenticated,service_role;
reset role;

-- These two narrowly granted functions need Storage metadata to reconcile
-- orphans and verify deletion. They are owned by the migration identity, with
-- fixed qualified queries, not by the worker/runtime owner. That owner still
-- receives no Storage SELECT grant or administrative capability.
create or replace function oshepherd_spike_api.maintenance_tick(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare cluster uuid; batch_limit integer; operation uuid; receipt oshepherd_spike.maintenance_operations;
  fingerprint bytea; response jsonb; purged jsonb; reaped integer; enqueued integer := 0;
  candidate record; scanned integer := 0; cursor_path text; paths jsonb := '[]'; deletion record;
begin
  perform oshepherd_spike.check_wire(p_request,array['cluster_id','limit']);
  perform oshepherd_spike.require_service();
  cluster := oshepherd_spike.uuid_field(p_request,'cluster_id');
  batch_limit := oshepherd_spike.integer_field(p_request,'limit',1,64);
  operation := oshepherd_spike.uuid_field(p_request,'operation_id');
  fingerprint := extensions.digest(pg_catalog.convert_to((p_request-'operation_id')::text,'UTF8'),'sha256');
  perform 1 from oshepherd_spike.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  select * into receipt from oshepherd_spike.maintenance_operations where operation_id = operation;
  if found then
    if receipt.cluster_id <> cluster or receipt.kind <> 'maintenance_tick' or receipt.request_hash <> fingerprint then
      raise sqlstate 'PT409' using message = 'idempotency_conflict';
    end if;
    return receipt.response;
  end if;
  reaped := oshepherd_spike.reap(cluster,batch_limit);
  purged := oshepherd_spike.purge(cluster,batch_limit);
  insert into oshepherd_spike.maintenance_cursors(cluster_id) values(cluster) on conflict do nothing;
  select object_cursor into cursor_path from oshepherd_spike.maintenance_cursors where cluster_id = cluster for update;
  -- Keyset scan, including young/referenced entries: inspect at most one page,
  -- never scan an unbounded bucket looking for a page of eligible orphans.
  -- Storage's (bucket_id,name COLLATE "C") index supports this bytewise
  -- namespace range. Locale ordering ignores punctuation and breaks it.
  for candidate in select name,created_at from storage.objects where bucket_id = 'oshepherd-spike-results'
    and name collate "C" >= (cluster::text || '/') collate "C"
    and name collate "C" < (cluster::text || '0') collate "C"
    and name collate "C" > cursor_path collate "C"
    order by name collate "C" limit batch_limit loop
    scanned := scanned+1; cursor_path := candidate.name;
    if candidate.created_at > pg_catalog.clock_timestamp()-interval '10 minutes'
      or candidate.name !~ '^([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/){3}result[.]json$'
      or exists(select 1 from oshepherd_spike.result_objects where object_path = candidate.name) then continue; end if;
    if exists(select 1 from oshepherd_spike.jobs j where j.cluster_id = cluster
        and j.id = pg_catalog.split_part(candidate.name,'/',2)::uuid
        and j.attempt_id = pg_catalog.split_part(candidate.name,'/',3)::uuid
        and j.state = 'running' and j.lease_until > pg_catalog.clock_timestamp()
        and j.execution_deadline > pg_catalog.clock_timestamp()
        and (j.mode = 'async' or j.consumer_until > pg_catalog.clock_timestamp())) then continue; end if;
    insert into oshepherd_spike.result_deletions(cluster_id,object_path)
      values(cluster,candidate.name) on conflict(object_path) do nothing;
    if found then enqueued := enqueued+1; end if;
  end loop;
  update oshepherd_spike.maintenance_cursors set object_cursor = case when scanned < batch_limit then '' else cursor_path end where cluster_id = cluster;
  for deletion in select d.object_path from oshepherd_spike.result_deletions d where d.cluster_id = cluster
    and d.available_at <= pg_catalog.clock_timestamp() and (d.leased_until is null or d.leased_until <= pg_catalog.clock_timestamp())
    and not exists(select 1 from oshepherd_spike.result_objects r where r.object_path = d.object_path)
    order by d.available_at,d.object_path limit batch_limit for update of d skip locked loop
    update oshepherd_spike.result_deletions set claim_id = operation,leased_until = pg_catalog.clock_timestamp()+interval '45 seconds',
      attempts = least(attempts+1,32) where object_path = deletion.object_path;
    paths := paths || pg_catalog.jsonb_build_array(deletion.object_path);
  end loop;
  delete from oshepherd_spike.maintenance_operations where operation_id in(
    select operation_id from oshepherd_spike.maintenance_operations where cluster_id = cluster and expires_at <= pg_catalog.clock_timestamp()
      order by expires_at,operation_id limit batch_limit);
  response := oshepherd_spike.answer(pg_catalog.jsonb_build_object('reaped',reaped,'purged',purged->'purged',
    'scanned',scanned,'enqueued',enqueued,'claim_id',operation,'object_paths',paths));
  insert into oshepherd_spike.maintenance_operations(operation_id,cluster_id,kind,request_hash,response)
    values(operation,cluster,'maintenance_tick',fingerprint,response);
  return response;
exception when unique_violation then raise sqlstate 'PT409' using message = 'idempotency_conflict';
end $$;

create or replace function oshepherd_spike_api.complete_maintenance(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare cluster uuid; claim uuid; operation uuid; receipt oshepherd_spike.maintenance_operations;
  fingerprint bytea; response jsonb; path record; deletion oshepherd_spike.result_deletions;
  deleted integer := 0; retrying integer := 0;
begin
  perform oshepherd_spike.check_wire(p_request,array['cluster_id','claim_id','deleted_paths','failed_paths']);
  perform oshepherd_spike.require_service();
  cluster := oshepherd_spike.uuid_field(p_request,'cluster_id');
  claim := oshepherd_spike.uuid_field(p_request,'claim_id');
  operation := oshepherd_spike.uuid_field(p_request,'operation_id');
  if pg_catalog.jsonb_typeof(p_request->'deleted_paths') is distinct from 'array'
    or pg_catalog.jsonb_typeof(p_request->'failed_paths') is distinct from 'array'
    or pg_catalog.jsonb_array_length(p_request->'deleted_paths')+pg_catalog.jsonb_array_length(p_request->'failed_paths') > 64 then
    raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  if exists(select 1 from pg_catalog.jsonb_array_elements((p_request->'deleted_paths')||(p_request->'failed_paths')) v
    where pg_catalog.jsonb_typeof(v) <> 'string' or pg_catalog.left(v#>>'{}',37) <> cluster::text || '/'
      or v#>>'{}' !~ '^([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/){3}result[.]json$')
    or (select count(*) <> count(distinct v) from pg_catalog.jsonb_array_elements((p_request->'deleted_paths')||(p_request->'failed_paths')) v) then
    raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  perform 1 from oshepherd_spike.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  fingerprint := extensions.digest(pg_catalog.convert_to((p_request-'operation_id')::text,'UTF8'),'sha256');
  select * into receipt from oshepherd_spike.maintenance_operations where operation_id = operation;
  if found then
    if receipt.cluster_id <> cluster or receipt.kind <> 'complete_maintenance' or receipt.request_hash <> fingerprint then
      raise sqlstate 'PT409' using message = 'idempotency_conflict'; end if;
    return receipt.response;
  end if;
  for path in select value as name,true as reported_deleted from pg_catalog.jsonb_array_elements_text(p_request->'deleted_paths')
    union all select value as name,false as reported_deleted from pg_catalog.jsonb_array_elements_text(p_request->'failed_paths') loop
    select * into deletion from oshepherd_spike.result_deletions where cluster_id = cluster and object_path = path.name for update;
    if not found then continue; end if;
    if deletion.claim_id is distinct from claim then raise sqlstate 'PT409' using message = 'stale_cleanup'; end if;
    -- HTTP 200 alone does not prove object deletion. Never forget intent while
    -- Storage metadata still exists (including partial/ambiguous deletions).
    if path.reported_deleted and not exists(select 1 from storage.objects where bucket_id = 'oshepherd-spike-results' and name = path.name) then
      delete from oshepherd_spike.result_deletions where object_path = path.name;
      deleted := deleted+1;
    else
      update oshepherd_spike.result_deletions set claim_id = null,leased_until = null,
        available_at = pg_catalog.clock_timestamp()+pg_catalog.make_interval(secs => least(300,pg_catalog.power(2,least(attempts,8))::integer))
        where object_path = path.name;
      retrying := retrying+1;
    end if;
  end loop;
  response := oshepherd_spike.answer(pg_catalog.jsonb_build_object('deleted',deleted,'retrying',retrying));
  insert into oshepherd_spike.maintenance_operations(operation_id,cluster_id,kind,request_hash,response)
    values(operation,cluster,'complete_maintenance',fingerprint,response);
  return response;
exception when unique_violation then raise sqlstate 'PT409' using message = 'idempotency_conflict';
end $$;

revoke all on function oshepherd_spike_api.maintenance_tick(jsonb),oshepherd_spike_api.complete_maintenance(jsonb) from public,anon,authenticated,service_role;
grant execute on function oshepherd_spike_api.maintenance_tick(jsonb),oshepherd_spike_api.complete_maintenance(jsonb) to service_role;
notify pgrst,'reload schema';
commit;
