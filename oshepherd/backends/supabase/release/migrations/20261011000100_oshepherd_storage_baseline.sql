-- Consolidated baseline for fresh installs, compatible with versioned Storage.
-- Supersedes the October 10 baseline without changing its applied bytes.
-- Initial versioned Oshepherd v1 candidate. Apply in one transaction.
-- Independent of the isolated local spike; no inference protocol change.
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_release_v1',0));
do $preflight$ begin
  if exists(select 1 from pg_catalog.pg_namespace where nspname in('oshepherd','oshepherd_api'))
    or exists(select 1 from pg_catalog.pg_roles where rolname = 'oshepherd_runtime')
    or exists(select 1 from storage.buckets where id = 'oshepherd-results')
    or exists(select 1 from pg_catalog.pg_policy where polname in('oshepherd_result_insert','oshepherd_result_verify')) then
    raise exception 'oshepherd_resource_collision';
  end if;
end $preflight$;


select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_schema_v1', 0));

do $$ begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'oshepherd_runtime') then
    create role oshepherd_runtime nologin nobypassrls;
  end if;
end $$;
grant oshepherd_runtime to postgres;
create schema if not exists oshepherd authorization oshepherd_runtime;
create schema if not exists oshepherd_api authorization oshepherd_runtime;
grant usage on schema extensions to oshepherd_runtime;
grant usage on schema oshepherd_api to authenticated, service_role;

create table if not exists oshepherd.clusters (
  id uuid primary key,
  owner_subject uuid not null references auth.users(id),
  enabled boolean not null default true,
  max_queued integer not null default 64 check (max_queued between 1 and 128),
  max_running integer not null default 64 check (max_running between 1 and 128),
  max_active integer not null default 128 check (max_active between 1 and 128),
  caller_max_active integer not null default 4 check (caller_max_active between 1 and 128),
  created_at timestamptz not null default pg_catalog.clock_timestamp()
);
create table if not exists oshepherd.memberships (
  cluster_id uuid not null references oshepherd.clusters(id),
  auth_subject uuid not null references auth.users(id),
  role text not null check (role in ('operator', 'worker')),
  revoked_at timestamptz,
  primary key (cluster_id, auth_subject)
);
create table if not exists oshepherd.api_keys (
  id uuid primary key,
  cluster_id uuid not null references oshepherd.clusters(id),
  key_hash bytea not null unique check (pg_catalog.octet_length(key_hash) = 32),
  revoked_at timestamptz,
  created_at timestamptz not null default pg_catalog.clock_timestamp()
);
create table if not exists oshepherd.workers (
  id uuid primary key default pg_catalog.gen_random_uuid(),
  auth_subject uuid not null unique references auth.users(id),
  cluster_id uuid not null references oshepherd.clusters(id),
  capacity integer not null check (capacity between 1 and 64),
  generation integer not null default 0 check (generation >= 0),
  session_id uuid,
  revoked_at timestamptz,
  draining boolean not null default false,
  heartbeat_at timestamptz,
  running jsonb not null default '{"models":[]}',
  version jsonb not null default '{}',
  created_at timestamptz not null default pg_catalog.clock_timestamp()
);
create table if not exists oshepherd.worker_sessions (
  worker_id uuid not null references oshepherd.workers(id),
  operation_id uuid not null,
  generation integer not null,
  session_id uuid not null,
  primary key (worker_id, operation_id)
);
create table if not exists oshepherd.worker_models (
  worker_id uuid not null references oshepherd.workers(id),
  name text not null,
  digest text not null,
  operations jsonb not null,
  details jsonb not null,
  show jsonb not null,
  observed_at timestamptz not null default pg_catalog.clock_timestamp(),
  primary key (worker_id, name)
);
create table if not exists oshepherd.admin_operations (
  operation_id uuid primary key,
  kind text not null,
  request_hash bytea not null,
  response jsonb not null
);

alter table oshepherd.clusters owner to oshepherd_runtime;
alter table oshepherd.memberships owner to oshepherd_runtime;
alter table oshepherd.api_keys owner to oshepherd_runtime;
alter table oshepherd.workers owner to oshepherd_runtime;
alter table oshepherd.worker_sessions owner to oshepherd_runtime;
alter table oshepherd.worker_models owner to oshepherd_runtime;
alter table oshepherd.admin_operations owner to oshepherd_runtime;
set local role oshepherd_runtime;

alter table oshepherd.clusters enable row level security;
alter table oshepherd.memberships enable row level security;
alter table oshepherd.api_keys enable row level security;
alter table oshepherd.workers enable row level security;
alter table oshepherd.worker_sessions enable row level security;
alter table oshepherd.worker_models enable row level security;
alter table oshepherd.admin_operations enable row level security;

create or replace function oshepherd.timestamp_wire(value timestamptz)
returns text language sql immutable set search_path = pg_catalog as $$
  select pg_catalog.to_char(value at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
$$;
create or replace function oshepherd.answer(value jsonb)
returns jsonb language sql immutable set search_path = pg_catalog as $$
  select pg_catalog.jsonb_build_object('v', 1, 'data', value)
$$;
create or replace function oshepherd.check_request(value jsonb, fields text[])
returns void language plpgsql set search_path = pg_catalog as $$
declare field text;
begin
  if pg_catalog.jsonb_typeof(value) is distinct from 'object' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  if value->>'v' is distinct from '1' or pg_catalog.jsonb_typeof(value->'v') is distinct from 'number' then
    raise sqlstate 'PT409' using message = 'unsupported_protocol';
  end if;
  if pg_catalog.octet_length(value::text) > 1048576 then
    raise sqlstate 'PT413' using message = 'request_too_large';
  end if;
  foreach field in array pg_catalog.array_cat(fields, array['v','operation_id']) loop
    if not value ? field or value->field = 'null'::jsonb then
      raise sqlstate 'PT422' using message = 'invalid_request';
    end if;
  end loop;
  if exists (select 1 from pg_catalog.jsonb_object_keys(value) as name where not name = any(pg_catalog.array_cat(fields, array['v','operation_id']))) then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
end $$;
create or replace function oshepherd.uuid_field(value jsonb, field text)
returns uuid language plpgsql set search_path = pg_catalog as $$
begin
  if pg_catalog.jsonb_typeof(value->field) is distinct from 'string' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  return (value->>field)::uuid;
exception when invalid_text_representation then
  raise sqlstate 'PT422' using message = 'invalid_request';
end $$;
create or replace function oshepherd.jwt_role()
returns text language sql stable set search_path = pg_catalog as $$
  select nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb->>'role'
$$;
create or replace function oshepherd.jwt_subject()
returns uuid language sql stable set search_path = pg_catalog as $$
  select (nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb->>'sub')::uuid
$$;
create or replace function oshepherd.require_service()
returns void language plpgsql set search_path = pg_catalog as $$
begin
  if oshepherd.jwt_role() is distinct from 'service_role' then
    raise sqlstate 'PT403' using message = 'forbidden';
  end if;
end $$;
create or replace function oshepherd.admin_replay(p_request jsonb, kind_name text)
returns jsonb language plpgsql set search_path = pg_catalog as $$
declare operation uuid; receipt oshepherd.admin_operations; fingerprint bytea;
begin
  perform oshepherd.require_service();
  operation := oshepherd.uuid_field(p_request, 'operation_id');
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(operation::text, 0));
  fingerprint := extensions.digest(pg_catalog.convert_to((p_request - 'operation_id')::text, 'UTF8'), 'sha256');
  select * into receipt from oshepherd.admin_operations where operation_id = operation;
  if found then
    if receipt.kind <> kind_name or receipt.request_hash <> fingerprint then
      raise sqlstate 'PT409' using message = 'idempotency_conflict';
    end if;
    return receipt.response;
  end if;
  return null;
end $$;
create or replace function oshepherd.admin_finish(p_request jsonb, kind_name text, value jsonb)
returns jsonb language plpgsql set search_path = pg_catalog as $$
declare response jsonb;
begin
  response := oshepherd.answer(value);
  insert into oshepherd.admin_operations(operation_id, kind, request_hash, response)
  values (oshepherd.uuid_field(p_request, 'operation_id'), kind_name,
    extensions.digest(pg_catalog.convert_to((p_request - 'operation_id')::text, 'UTF8'), 'sha256'), response);
  return response;
end $$;

create or replace function oshepherd.current_worker(p_request jsonb, check_session boolean default true)
returns oshepherd.workers language plpgsql set search_path = pg_catalog as $$
declare worker oshepherd.workers;
begin
  if oshepherd.jwt_role() is distinct from 'authenticated' or oshepherd.jwt_subject() is null then
    raise sqlstate 'PT401' using message = 'unauthorized';
  end if;
  select * into worker from oshepherd.workers where auth_subject = oshepherd.jwt_subject();
  if not found then raise sqlstate 'PT403' using message = 'forbidden'; end if;
  if worker.revoked_at is not null or not exists (
    select 1 from oshepherd.memberships where cluster_id = worker.cluster_id
      and auth_subject = worker.auth_subject and role = 'worker' and revoked_at is null
  ) then raise sqlstate 'PT403' using message = 'worker_revoked'; end if;
  if check_session and (
    worker.session_id is distinct from oshepherd.uuid_field(p_request, 'session_id')
    or pg_catalog.jsonb_typeof(p_request->'generation') is distinct from 'number'
    or worker.generation::text is distinct from p_request->>'generation'
  ) then raise sqlstate 'PT409' using message = 'stale_session'; end if;
  return worker;
end $$;

create or replace function oshepherd_api.bootstrap_cluster(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare replay jsonb; cluster uuid; owner_id uuid;
begin
  perform oshepherd.check_request(p_request, array['cluster_id','owner_subject']);
  replay := oshepherd.admin_replay(p_request, 'bootstrap_cluster');
  if replay is not null then return replay; end if;
  cluster := oshepherd.uuid_field(p_request, 'cluster_id');
  owner_id := oshepherd.uuid_field(p_request, 'owner_subject');
  insert into oshepherd.clusters(id, owner_subject) values (cluster, owner_id);
  insert into oshepherd.memberships(cluster_id, auth_subject, role) values (cluster, owner_id, 'operator');
  return oshepherd.admin_finish(p_request, 'bootstrap_cluster', pg_catalog.jsonb_build_object('cluster_id', cluster));
exception when unique_violation then raise sqlstate 'PT409' using message = 'idempotency_conflict';
  when foreign_key_violation then raise sqlstate 'PT422' using message = 'invalid_request';
end $$;
create or replace function oshepherd_api.create_api_key(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare replay jsonb; cluster uuid; key_id uuid; raw_key text;
begin
  perform oshepherd.check_request(p_request, array['cluster_id','key_id','raw_key']);
  replay := oshepherd.admin_replay(p_request, 'create_api_key');
  if replay is not null then return replay; end if;
  cluster := oshepherd.uuid_field(p_request, 'cluster_id');
  key_id := oshepherd.uuid_field(p_request, 'key_id');
  raw_key := p_request->>'raw_key';
  if pg_catalog.jsonb_typeof(p_request->'raw_key') is distinct from 'string' or raw_key !~ '^osk_[A-Za-z0-9_-]{43}$' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  perform 1 from oshepherd.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  insert into oshepherd.api_keys(id, cluster_id, key_hash)
    values (key_id, cluster, extensions.digest(pg_catalog.convert_to(raw_key, 'UTF8'), 'sha256'));
  return oshepherd.admin_finish(p_request, 'create_api_key', pg_catalog.jsonb_build_object('key_id', key_id, 'cluster_id', cluster));
exception when unique_violation then raise sqlstate 'PT409' using message = 'idempotency_conflict';
end $$;
create or replace function oshepherd_api.enroll_worker(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare replay jsonb; cluster uuid; subject uuid; worker_id uuid; capacity integer;
begin
  perform oshepherd.check_request(p_request, array['cluster_id','auth_subject','capacity']);
  replay := oshepherd.admin_replay(p_request, 'enroll_worker');
  if replay is not null then return replay; end if;
  cluster := oshepherd.uuid_field(p_request, 'cluster_id');
  subject := oshepherd.uuid_field(p_request, 'auth_subject');
  if pg_catalog.jsonb_typeof(p_request->'capacity') is distinct from 'number' or p_request->>'capacity' !~ '^[0-9]{1,2}$' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  capacity := (p_request->>'capacity')::integer;
  if capacity not between 1 and 64 then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  perform 1 from oshepherd.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  insert into oshepherd.memberships(cluster_id, auth_subject, role) values (cluster, subject, 'worker');
  insert into oshepherd.workers(cluster_id, auth_subject, capacity) values (cluster, subject, capacity) returning id into worker_id;
  return oshepherd.admin_finish(p_request, 'enroll_worker', pg_catalog.jsonb_build_object('worker_id', worker_id, 'cluster_id', cluster, 'capacity', capacity));
exception when unique_violation then raise sqlstate 'PT409' using message = 'idempotency_conflict';
  when foreign_key_violation then raise sqlstate 'PT422' using message = 'invalid_request';
end $$;
create or replace function oshepherd_api.revoke_worker(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare replay jsonb; cluster uuid; worker_id uuid; subject uuid;
begin
  perform oshepherd.check_request(p_request, array['cluster_id','worker_id']);
  replay := oshepherd.admin_replay(p_request, 'revoke_worker');
  if replay is not null then return replay; end if;
  cluster := oshepherd.uuid_field(p_request, 'cluster_id');
  worker_id := oshepherd.uuid_field(p_request, 'worker_id');
  perform 1 from oshepherd.clusters where id = cluster for update;
  update oshepherd.workers set revoked_at = pg_catalog.clock_timestamp(), draining = true
    where id = worker_id and cluster_id = cluster returning auth_subject into subject;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  update oshepherd.memberships set revoked_at = pg_catalog.clock_timestamp()
    where cluster_id = cluster and auth_subject = subject;
  return oshepherd.admin_finish(p_request, 'revoke_worker', pg_catalog.jsonb_build_object('worker_id', worker_id, 'revoked', true));
end $$;
create or replace function oshepherd_api.register_worker(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd.workers; receipt oshepherd.worker_sessions; operation uuid;
begin
  perform oshepherd.check_request(p_request, array[]::text[]);
  operation := oshepherd.uuid_field(p_request, 'operation_id');
  worker := oshepherd.current_worker(p_request, false);
  perform 1 from oshepherd.clusters where id = worker.cluster_id for update;
  perform 1 from oshepherd.workers where id = worker.id for update;
  worker := oshepherd.current_worker(p_request, false);
  select * into receipt from oshepherd.worker_sessions where worker_id = worker.id and operation_id = operation;
  if found then
    if receipt.session_id <> worker.session_id or receipt.generation <> worker.generation then
      raise sqlstate 'PT409' using message = 'stale_session';
    end if;
  else
    update oshepherd.workers set generation = generation + 1, session_id = pg_catalog.gen_random_uuid(),
      heartbeat_at = pg_catalog.clock_timestamp(), draining = false where id = worker.id returning * into worker;
    insert into oshepherd.worker_sessions(worker_id, operation_id, generation, session_id)
      values (worker.id, operation, worker.generation, worker.session_id);
  end if;
  return oshepherd.answer(pg_catalog.jsonb_build_object(
    'worker_id', worker.id, 'cluster_id', worker.cluster_id, 'capacity', worker.capacity,
    'session_id', worker.session_id, 'generation', worker.generation,
    'server_now', oshepherd.timestamp_wire(pg_catalog.clock_timestamp()), 'worker_lease_ms', 45000
  ));
end $$;

reset role;
revoke all on all tables in schema oshepherd from public, anon, authenticated, service_role;
revoke all on all functions in schema oshepherd from public, anon, authenticated, service_role;
revoke all on all functions in schema oshepherd_api from public, anon, authenticated, service_role;
grant execute on function oshepherd_api.bootstrap_cluster(jsonb), oshepherd_api.create_api_key(jsonb),
  oshepherd_api.enroll_worker(jsonb), oshepherd_api.revoke_worker(jsonb) to service_role;
grant execute on function oshepherd_api.register_worker(jsonb) to authenticated;
notify pgrst, 'reload schema';

select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_schema_v1', 0));
set local role oshepherd_runtime;

create table if not exists oshepherd.jobs (
  id uuid primary key default pg_catalog.gen_random_uuid(),
  cluster_id uuid not null references oshepherd.clusters(id),
  key_id uuid not null references oshepherd.api_keys(id),
  submit_operation uuid not null,
  idempotency_key text,
  request_hash bytea not null,
  request jsonb not null,
  mode text not null check (mode in ('sync','async')),
  model_name text not null,
  model_digest text not null,
  stream boolean not null,
  state text not null default 'queued' check (state in ('queued','running','succeeded','failed','cancelled','expired')),
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  queue_deadline timestamptz not null,
  execution_deadline timestamptz not null,
  available_at timestamptz not null default pg_catalog.clock_timestamp(),
  consumer_token uuid,
  consumer_until timestamptz,
  attempts integer not null default 0 check (attempts between 0 and 6),
  worker_id uuid references oshepherd.workers(id),
  generation integer,
  session_id uuid,
  attempt_id uuid,
  lease_until timestamptz,
  last_seq integer not null default 0,
  output_bytes integer not null default 0,
  max_output_bytes integer not null check (max_output_bytes between 1 and 33554432),
  result jsonb,
  error jsonb,
  finished_at timestamptz,
  content_expires_at timestamptz,
  tombstone_expires_at timestamptz,
  unique (key_id, submit_operation),
  unique (key_id, idempotency_key)
);
create index if not exists jobs_ready on oshepherd.jobs(cluster_id, state, available_at, created_at);
create index if not exists jobs_worker on oshepherd.jobs(worker_id, state);
alter table oshepherd.jobs add column if not exists updated_at timestamptz not null default pg_catalog.clock_timestamp();
alter table oshepherd.jobs add column if not exists queue_budget_ms integer;
update oshepherd.jobs set queue_budget_ms = greatest(1, least(3600000,
  round(extract(epoch from (queue_deadline-created_at))*1000)::integer)) where queue_budget_ms is null;
alter table oshepherd.jobs alter column queue_budget_ms set not null;
do $$ begin
  if not exists(select 1 from pg_catalog.pg_constraint where conrelid = 'oshepherd.jobs'::regclass and conname = 'jobs_queue_budget') then
    alter table oshepherd.jobs add constraint jobs_queue_budget check(queue_budget_ms between 1 and 3600000);
  end if;
end $$;
create or replace function oshepherd.touch_job()
returns trigger language plpgsql set search_path = pg_catalog as $$
begin new.updated_at := pg_catalog.clock_timestamp(); return new; end $$;
create or replace trigger touch_job before update on oshepherd.jobs for each row execute function oshepherd.touch_job();
create table if not exists oshepherd.chunks (
  job_id uuid not null references oshepherd.jobs(id) on delete cascade,
  seq integer not null,
  attempt_id uuid not null,
  content text not null,
  checksum text not null,
  size_bytes integer not null check (size_bytes between 1 and 65536),
  primary key (job_id, seq)
);
create table if not exists oshepherd.worker_operations (
  worker_id uuid not null references oshepherd.workers(id),
  operation_id uuid not null,
  job_id uuid references oshepherd.jobs(id) on delete cascade,
  request_hash bytea not null,
  kind text not null,
  response jsonb not null,
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  primary key (worker_id, operation_id)
);
create index if not exists worker_operations_age on oshepherd.worker_operations(created_at,worker_id,operation_id);
create index if not exists worker_operations_job on oshepherd.worker_operations(job_id);
create or replace function oshepherd.claim_receipt(response jsonb)
returns jsonb language sql immutable set search_path = pg_catalog as $$
  select pg_catalog.jsonb_set(response, '{data,jobs}', coalesce((
    select pg_catalog.jsonb_agg(claim - 'request' order by ordinal)
    from pg_catalog.jsonb_array_elements(response #> '{data,jobs}') with ordinality as c(claim,ordinal)
  ),'[]'::jsonb))
$$;
update oshepherd.worker_operations set response = oshepherd.claim_receipt(response)
  where kind = 'heartbeat_and_claim' and exists(
    select 1 from pg_catalog.jsonb_array_elements(response #> '{data,jobs}') c where c ? 'request');
create index if not exists jobs_expiry on oshepherd.jobs(cluster_id,content_expires_at,id);
create table if not exists oshepherd.result_deletions (
  cluster_id uuid not null references oshepherd.clusters(id) on delete cascade,
  object_path text primary key,
  available_at timestamptz not null default pg_catalog.clock_timestamp()+interval '5 minutes',
  claim_id uuid,
  leased_until timestamptz,
  attempts integer not null default 0 check(attempts >= 0),
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  check(object_path ~ '^[a-f0-9-]{36}/[a-f0-9-]{36}/[a-f0-9-]{36}/result[.]json$'),
  check(pg_catalog.left(object_path,37) = cluster_id::text || '/')
);
create index if not exists result_deletions_ready on oshepherd.result_deletions(cluster_id,available_at,leased_until);
alter table oshepherd.jobs enable row level security;
alter table oshepherd.chunks enable row level security;
alter table oshepherd.worker_operations enable row level security;
alter table oshepherd.result_deletions enable row level security;

create or replace function oshepherd.check_fields(value jsonb, fields text[], nullable text[] default array[]::text[])
returns void language plpgsql set search_path = pg_catalog as $$
declare field text;
begin
  if pg_catalog.jsonb_typeof(value) is distinct from 'object' or
    pg_catalog.octet_length(value::text) > 4194304 then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  foreach field in array fields loop
    if not value ? field or (value->field = 'null'::jsonb and not field = any(nullable)) then
      raise sqlstate 'PT422' using message = 'invalid_request';
    end if;
  end loop;
  if exists (select 1 from pg_catalog.jsonb_object_keys(value) as name where not name = any(fields)) then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
end $$;
create or replace function oshepherd.check_wire(value jsonb, fields text[], nullable text[] default array[]::text[])
returns void language plpgsql set search_path = pg_catalog as $$
begin
  -- Caller requests stay at 1 MiB. A final 1 MiB record can expand when encoded
  -- as JSON strings; only the completion RPC gets the larger control envelope.
  if pg_catalog.octet_length(value::text) > (case when fields @> array['final_chunks','result'] then 4194304 else 1048576 end) then
    raise sqlstate 'PT413' using message = 'request_too_large';
  end if;
  perform oshepherd.check_fields(value, pg_catalog.array_cat(fields, array['v','operation_id']), nullable);
  if value->>'v' is distinct from '1' or pg_catalog.jsonb_typeof(value->'v') is distinct from 'number' then
    raise sqlstate 'PT409' using message = 'unsupported_protocol';
  end if;
  perform oshepherd.uuid_field(value, 'operation_id');
end $$;
create or replace function oshepherd.integer_field(value jsonb, field text, low integer, high integer)
returns integer language plpgsql set search_path = pg_catalog as $$
declare result bigint;
begin
  if pg_catalog.jsonb_typeof(value->field) is distinct from 'number' or value->>field !~ '^[0-9]{1,10}$' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  result := (value->>field)::bigint;
  if result < low or result > high then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  return result::integer;
end $$;
create or replace function oshepherd.lock_worker(p_request jsonb)
returns oshepherd.workers language plpgsql set search_path = pg_catalog as $$
declare worker oshepherd.workers;
begin
  worker := oshepherd.current_worker(p_request);
  perform 1 from oshepherd.clusters where id = worker.cluster_id for update;
  perform 1 from oshepherd.workers where id = worker.id for update;
  return oshepherd.current_worker(p_request);
end $$;
create or replace function oshepherd.caller(p_request jsonb)
returns oshepherd.api_keys language plpgsql set search_path = pg_catalog as $$
declare caller oshepherd.api_keys; cluster uuid;
begin
  perform oshepherd.require_service();
  cluster := oshepherd.uuid_field(p_request, 'cluster_id');
  if pg_catalog.jsonb_typeof(p_request->'key') is distinct from 'string' or pg_catalog.length(p_request->>'key') > 256 then
    raise sqlstate 'PT401' using message = 'unauthorized';
  end if;
  perform 1 from oshepherd.clusters where id = cluster and enabled for update;
  if not found then raise sqlstate 'PT401' using message = 'unauthorized'; end if;
  select * into caller from oshepherd.api_keys where cluster_id = cluster and revoked_at is null
    and key_hash = extensions.digest(pg_catalog.convert_to(p_request->>'key', 'UTF8'), 'sha256');
  if not found then raise sqlstate 'PT401' using message = 'unauthorized'; end if;
  return caller;
end $$;
create or replace function oshepherd.worker_replay(p_request jsonb, worker uuid, kind_name text)
returns jsonb language plpgsql set search_path = pg_catalog as $$
declare receipt oshepherd.worker_operations;
begin
  select * into receipt from oshepherd.worker_operations
    where worker_id = worker and operation_id = oshepherd.uuid_field(p_request, 'operation_id');
  if found then
    if receipt.kind <> kind_name or receipt.request_hash <> extensions.digest(pg_catalog.convert_to(p_request::text, 'UTF8'), 'sha256') then
      raise sqlstate 'PT409' using message = 'idempotency_conflict';
    end if;
    return receipt.response;
  end if;
  return null;
end $$;
create or replace function oshepherd.worker_finish(p_request jsonb, worker uuid, kind_name text, job uuid, value jsonb)
returns jsonb language plpgsql set search_path = pg_catalog as $$
declare response jsonb;
begin
  response := oshepherd.answer(value);
  insert into oshepherd.worker_operations(worker_id, operation_id, job_id, request_hash, kind, response)
    values(worker, oshepherd.uuid_field(p_request, 'operation_id'), job,
      extensions.digest(pg_catalog.convert_to(p_request::text, 'UTF8'), 'sha256'), kind_name,
      case when kind_name = 'heartbeat_and_claim' then oshepherd.claim_receipt(response) else response end);
  return response;
end $$;
create or replace function oshepherd.terminal(job uuid, terminal_state text, problem jsonb default null)
returns void language plpgsql set search_path = pg_catalog as $$
begin
  update oshepherd.jobs set state = terminal_state, error = problem,
    finished_at = pg_catalog.clock_timestamp(), lease_until = null,
    content_expires_at = pg_catalog.clock_timestamp() + case when mode = 'async' then interval '1 day' else interval '30 minutes' end,
    tombstone_expires_at = pg_catalog.clock_timestamp() + interval '2 days'
    where id = job and state in ('queued','running');
end $$;
create or replace function oshepherd.retry_or_fail(job oshepherd.jobs, retryable boolean, problem jsonb)
returns void language plpgsql set search_path = pg_catalog as $$
declare delay_seconds integer; now_at timestamptz := pg_catalog.clock_timestamp(); next_available timestamptz;
begin
  delay_seconds := least(60, (2 ^ greatest(0, job.attempts - 1))::integer);
  next_available := now_at + pg_catalog.make_interval(secs => delay_seconds);
  if retryable and job.output_bytes = 0 and job.attempts < 6
    and job.execution_deadline > next_available then
    update oshepherd.jobs set state = 'queued', worker_id = null, session_id = null, generation = null,
      attempt_id = null, lease_until = null, available_at = next_available,
      queue_deadline = least(execution_deadline, next_available + pg_catalog.make_interval(secs => job.queue_budget_ms/1000.0)),
      error = problem where id = job.id;
  else
    perform oshepherd.terminal(job.id, 'failed', problem);
  end if;
end $$;
create or replace function oshepherd.reap(cluster uuid, batch_limit integer)
returns integer language plpgsql set search_path = pg_catalog as $$
declare job oshepherd.jobs; count integer := 0; now_at timestamptz := pg_catalog.clock_timestamp();
begin
  for job in select j.* from oshepherd.jobs j left join oshepherd.workers w on w.id = j.worker_id
    where j.cluster_id = cluster and j.state in ('queued','running') and (
      j.execution_deadline <= now_at or (j.state = 'queued' and j.queue_deadline <= now_at)
      or (j.mode = 'sync' and j.consumer_until <= now_at)
      or (j.state = 'running' and (j.lease_until <= now_at or w.revoked_at is not null
        or w.generation is distinct from j.generation or w.session_id is distinct from j.session_id)))
    order by j.id limit batch_limit for update of j loop
    count := count + 1;
    if job.execution_deadline <= now_at or (job.state = 'queued' and job.queue_deadline <= now_at) then
      perform oshepherd.terminal(job.id, 'expired', '{"code":"execution_expired","message":"Job deadline elapsed"}');
    elsif job.mode = 'sync' and job.consumer_until <= now_at then
      perform oshepherd.terminal(job.id, 'cancelled', '{"code":"consumer_expired","message":"Consumer lease elapsed"}');
    else
      perform oshepherd.retry_or_fail(job, true, '{"code":"worker_lost","message":"Worker lease elapsed"}');
    end if;
  end loop;
  return count;
end $$;
create or replace function oshepherd.job_wire(job oshepherd.jobs)
returns jsonb language sql stable set search_path = pg_catalog as $$
  select pg_catalog.jsonb_build_object('job_id', job.id, 'state', job.state, 'mode', job.mode,
    'created_at', oshepherd.timestamp_wire(job.created_at),
    'updated_at', oshepherd.timestamp_wire(job.updated_at),
    'execution_deadline', oshepherd.timestamp_wire(job.execution_deadline),
    'finished_at', oshepherd.timestamp_wire(job.finished_at),
    'content_expires_at', oshepherd.timestamp_wire(job.content_expires_at),
    'content_available', job.content_expires_at is null or job.content_expires_at > pg_catalog.clock_timestamp(),
    'attempts', job.attempts, 'last_seq', job.last_seq, 'output_bytes', job.output_bytes, 'error', job.error)
$$;
create or replace function oshepherd.attempt(p_request jsonb, worker oshepherd.workers)
returns oshepherd.jobs language plpgsql set search_path = pg_catalog as $$
declare job oshepherd.jobs;
begin
  select * into job from oshepherd.jobs where id = oshepherd.uuid_field(p_request, 'job_id')
    and cluster_id = worker.cluster_id for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  if job.state <> 'running' or job.worker_id <> worker.id or job.session_id <> worker.session_id
    or job.generation <> worker.generation or job.attempt_id <> oshepherd.uuid_field(p_request, 'attempt_id')
    or job.lease_until <= pg_catalog.clock_timestamp() or job.execution_deadline <= pg_catalog.clock_timestamp()
    or (job.mode = 'sync' and job.consumer_until <= pg_catalog.clock_timestamp()) then
    raise sqlstate 'PT409' using message = 'stale_attempt';
  end if;
  return job;
end $$;

create or replace function oshepherd_api.update_models(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd.workers; model jsonb; replay jsonb;
begin
  perform oshepherd.check_wire(p_request, array['session_id','generation','models','running','version']);
  worker := oshepherd.lock_worker(p_request);
  replay := oshepherd.worker_replay(p_request, worker.id, 'update_models');
  if replay is not null then return replay; end if;
  if pg_catalog.jsonb_typeof(p_request->'models') is distinct from 'array' or pg_catalog.jsonb_array_length(p_request->'models') > 256
    or pg_catalog.jsonb_typeof(p_request->'running') is distinct from 'object' or pg_catalog.jsonb_typeof(p_request->'version') is distinct from 'object' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  delete from oshepherd.worker_models where worker_id = worker.id;
  for model in select value from pg_catalog.jsonb_array_elements(p_request->'models') loop
    perform oshepherd.check_fields(model, array['name','digest','operations','details','show']);
    if pg_catalog.jsonb_typeof(model->'name') is distinct from 'string' or pg_catalog.length(model->>'name') not between 1 and 256
      or pg_catalog.jsonb_typeof(model->'digest') is distinct from 'string' or model->>'digest' !~ '^[a-f0-9]{64}$'
      or pg_catalog.jsonb_typeof(model->'operations') is distinct from 'array' or pg_catalog.jsonb_array_length(model->'operations') > 4
      or exists(select 1 from pg_catalog.jsonb_array_elements_text(model->'operations') op where op not in ('generate','chat','embeddings','systemone'))
      or pg_catalog.jsonb_typeof(model->'details') is distinct from 'object' or pg_catalog.jsonb_typeof(model->'show') is distinct from 'object' then
      raise sqlstate 'PT422' using message = 'invalid_request';
    end if;
    insert into oshepherd.worker_models(worker_id, name, digest, operations, details, show)
      values(worker.id, model->>'name', model->>'digest', model->'operations', model->'details', model->'show');
  end loop;
  update oshepherd.workers set running = p_request->'running', version = p_request->'version',
    heartbeat_at = pg_catalog.clock_timestamp() where id = worker.id;
  return oshepherd.worker_finish(p_request, worker.id, 'update_models', null, '{"updated":true}');
exception when unique_violation then raise sqlstate 'PT422' using message = 'invalid_request';
end $$;

create or replace function oshepherd_api.submit_job(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare caller oshepherd.api_keys; cluster oshepherd.clusters; job oshepherd.jobs;
  limits jsonb; fingerprint bytea; model_name text; digest text; digest_count integer; token uuid;
  queue_ms integer; total_ms integer; max_bytes integer; now_at timestamptz := pg_catalog.clock_timestamp();
begin
  perform oshepherd.check_wire(p_request, array['cluster_id','key','request','mode','limits','idempotency_key','consumer_token'], array['idempotency_key','consumer_token']);
  caller := oshepherd.caller(p_request);
  select * into cluster from oshepherd.clusters where id = caller.cluster_id;
  perform oshepherd.check_fields(p_request->'request', array['type','payload']);
  if p_request->>'mode' not in ('sync','async') or p_request #>> '{request,type}' not in ('generate','chat','embeddings','systemone')
    or pg_catalog.jsonb_typeof(p_request #> '{request,payload}') is distinct from 'object'
    or pg_catalog.jsonb_typeof(p_request #> '{request,payload,model}') is distinct from 'string'
    or pg_catalog.length(p_request #>> '{request,payload,model}') not between 1 and 256 then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  if p_request #> '{request,payload,stream}' is not null and p_request #> '{request,payload,stream}' <> 'null'::jsonb
    and pg_catalog.jsonb_typeof(p_request #> '{request,payload,stream}') <> 'boolean' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  if p_request->>'mode' = 'async' and p_request #>> '{request,type}' in ('generate','chat') and p_request #> '{request,payload,stream}' = 'true'::jsonb then
    raise sqlstate 'PT422' using message = 'async_streaming_unsupported';
  end if;
  if pg_catalog.jsonb_typeof(p_request->'limits') is distinct from 'object' or exists(
    select 1 from pg_catalog.jsonb_object_keys(p_request->'limits') n where n not in ('queue_ms','total_ms','max_output_bytes')) then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  limits := pg_catalog.jsonb_build_object('queue_ms', case when p_request->>'mode' = 'async' then 60000 else 30000 end,
    'total_ms', case when p_request->>'mode' = 'async' then 3600000 else 135000 end, 'max_output_bytes', 33554432) || (p_request->'limits');
  queue_ms := oshepherd.integer_field(limits, 'queue_ms', 1, 3600000);
  total_ms := oshepherd.integer_field(limits, 'total_ms', 1, case when p_request->>'mode' = 'async' then 86400000 else 135000 end);
  max_bytes := oshepherd.integer_field(limits, 'max_output_bytes', 1, 33554432);
  fingerprint := extensions.digest(pg_catalog.convert_to(pg_catalog.jsonb_build_object('request',p_request->'request','mode',p_request->'mode','limits',limits)::text,'UTF8'),'sha256');
  if p_request->'idempotency_key' <> 'null'::jsonb and (pg_catalog.jsonb_typeof(p_request->'idempotency_key') <> 'string'
    or pg_catalog.length(p_request->>'idempotency_key') not between 1 and 128) then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  if p_request->'consumer_token' <> 'null'::jsonb then token := oshepherd.uuid_field(p_request,'consumer_token'); end if;
  if (p_request->>'mode' = 'sync') <> (token is not null) then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  select * into job from oshepherd.jobs where key_id = caller.id and
    (submit_operation = oshepherd.uuid_field(p_request,'operation_id') or idempotency_key = p_request->>'idempotency_key');
  if found then
    if job.request_hash <> fingerprint then raise sqlstate 'PT409' using message = 'idempotency_conflict'; end if;
    -- Consumer identity is never disclosed or replaced by an idempotency replay.
    if job.mode = 'sync' and job.consumer_token is distinct from token then raise sqlstate 'PT409' using message = 'consumer_conflict'; end if;
    return oshepherd.answer(oshepherd.job_wire(job));
  end if;
  perform oshepherd.reap(cluster.id, 128);
  if (select count(*) from oshepherd.jobs where cluster_id = cluster.id and state in ('queued','running')) >= cluster.max_active
    or (select count(*) from oshepherd.jobs where cluster_id = cluster.id and state = 'queued') >= cluster.max_queued
    or (select count(*) from oshepherd.jobs where key_id = caller.id and state in ('queued','running')) >= cluster.caller_max_active then
    raise sqlstate 'PT429' using message = 'capacity_exceeded';
  end if;
  model_name := p_request #>> '{request,payload,model}';
  if pg_catalog.strpos(model_name, ':') = 0 then model_name := model_name || ':latest'; end if;
  select count(distinct m.digest), min(m.digest) into digest_count, digest from oshepherd.worker_models m
    join oshepherd.workers w on w.id = m.worker_id where w.cluster_id = cluster.id and w.revoked_at is null
      and not w.draining and w.heartbeat_at > now_at - interval '60 seconds' and m.observed_at > now_at - interval '60 seconds'
      and m.name = model_name and m.operations ? (p_request #>> '{request,type}');
  if digest_count = 0 then raise sqlstate 'PT503' using message = 'model_unavailable'; end if;
  if digest_count > 1 then raise sqlstate 'PT409' using message = 'model_digest_conflict'; end if;
  insert into oshepherd.jobs(cluster_id, key_id, submit_operation, idempotency_key, request_hash, request,
    mode, model_name, model_digest, stream, queue_budget_ms, queue_deadline, execution_deadline, consumer_token, consumer_until, max_output_bytes)
    values(cluster.id, caller.id, oshepherd.uuid_field(p_request,'operation_id'), p_request->>'idempotency_key', fingerprint,
      p_request->'request', p_request->>'mode', model_name, digest, (p_request #>> '{request,type}' in ('generate','chat')) and coalesce(p_request #> '{request,payload,stream}' = 'true'::jsonb,false),
      queue_ms, now_at + pg_catalog.make_interval(secs => least(queue_ms,total_ms)/1000.0), now_at + pg_catalog.make_interval(secs => total_ms/1000.0),
      token, case when token is not null then now_at + interval '60 seconds' end, max_bytes) returning * into job;
  return oshepherd.answer(oshepherd.job_wire(job));
end $$;

create or replace function oshepherd_api.heartbeat_and_claim(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd.workers; job oshepherd.jobs; active jsonb; replay jsonb; candidate jsonb; attempt uuid;
  claims jsonb := '[]'; stopped jsonb := '[]'; maximum integer; slots integer; response_bytes integer; candidate_bytes integer;
  now_at timestamptz := pg_catalog.clock_timestamp();
begin
  perform oshepherd.check_wire(p_request,array['session_id','generation','active_attempts','max_jobs','draining']);
  worker := oshepherd.lock_worker(p_request);
  maximum := oshepherd.integer_field(p_request,'max_jobs',0,64);
  if pg_catalog.jsonb_typeof(p_request->'draining') is distinct from 'boolean'
    or pg_catalog.jsonb_typeof(p_request->'active_attempts') is distinct from 'array'
    or pg_catalog.jsonb_array_length(p_request->'active_attempts') > 64 then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  perform oshepherd.reap(worker.cluster_id,128);
  update oshepherd.workers set heartbeat_at = now_at, draining = (p_request->>'draining')::boolean where id = worker.id;
  for active in select value from pg_catalog.jsonb_array_elements(p_request->'active_attempts') loop
    perform oshepherd.check_fields(active,array['job_id','attempt_id']);
    update oshepherd.jobs set lease_until = least(execution_deadline, now_at + interval '45 seconds')
      where id = oshepherd.uuid_field(active,'job_id') and attempt_id = oshepherd.uuid_field(active,'attempt_id')
      and worker_id = worker.id and generation = worker.generation and session_id = worker.session_id and state = 'running';
    if not found then stopped := stopped || pg_catalog.jsonb_build_array(active); end if;
  end loop;
  -- Replaying a lost claim response returns the assigned attempt without a
  -- second admission. Leases renew only for the caller's reported active set.
  replay := oshepherd.worker_replay(p_request,worker.id,'heartbeat_and_claim');
  if replay is not null then
    for active in select value from pg_catalog.jsonb_array_elements(replay #> '{data,jobs}') loop
      select * into job from oshepherd.jobs where id = (active->>'job_id')::uuid and attempt_id = (active->>'attempt_id')::uuid
        and state = 'running' and worker_id = worker.id and generation = worker.generation and session_id = worker.session_id;
      if not found then raise sqlstate 'PT409' using message = 'stale_attempt'; end if;
      claims := claims || pg_catalog.jsonb_build_array(active || pg_catalog.jsonb_build_object('request',job.request));
    end loop;
    return pg_catalog.jsonb_set(replay,'{data,jobs}',claims);
  end if;
  slots := least(maximum, worker.capacity - (select count(*) from oshepherd.jobs where worker_id = worker.id and state = 'running'),
    (select max_running from oshepherd.clusters where id = worker.cluster_id) -
      (select count(*) from oshepherd.jobs where cluster_id = worker.cluster_id and state = 'running'));
  if not (p_request->>'draining')::boolean and slots > 0 then
    -- RPC_RESPONSE_BYTES (Python/TypeScript): budget the complete jsonb wire
    -- envelope, including stop entries, before assigning any candidate job.
    response_bytes := pg_catalog.octet_length(oshepherd.answer(pg_catalog.jsonb_build_object(
      'jobs',claims,'stop',stopped,'server_now',oshepherd.timestamp_wire(now_at)))::text);
    for job in select j.* from oshepherd.jobs j where j.cluster_id = worker.cluster_id and j.state = 'queued' and j.available_at <= now_at
      and j.queue_deadline > now_at and j.execution_deadline > now_at and exists(select 1 from oshepherd.worker_models m
        where m.worker_id = worker.id and m.name = j.model_name and m.digest = j.model_digest
        and m.observed_at > now_at - interval '60 seconds' and m.operations ? (j.request->>'type'))
      order by j.created_at,j.id limit slots for update loop
      attempt := pg_catalog.gen_random_uuid();
      candidate := pg_catalog.jsonb_build_object('job_id',job.id,'attempt_id',attempt,
        'request',job.request,'mode',job.mode,'model_digest',job.model_digest,'execution_deadline',oshepherd.timestamp_wire(job.execution_deadline),
        'max_output_bytes',job.max_output_bytes);
      candidate_bytes := pg_catalog.octet_length(candidate::text) + case when claims = '[]'::jsonb then 0 else 2 end;
      if response_bytes + candidate_bytes > 2097152 then exit; end if;
      update oshepherd.jobs set state = 'running', worker_id = worker.id, generation = worker.generation, session_id = worker.session_id,
        attempt_id = attempt, attempts = attempts + 1, error = null,
        lease_until = least(execution_deadline,now_at + interval '45 seconds') where id = job.id returning * into job;
      claims := claims || pg_catalog.jsonb_build_array(candidate);
      response_bytes := response_bytes + candidate_bytes;
    end loop;
  end if;
  return oshepherd.worker_finish(p_request,worker.id,'heartbeat_and_claim',null,
    pg_catalog.jsonb_build_object('jobs',claims,'stop',stopped,'server_now',oshepherd.timestamp_wire(now_at)));
end $$;

create or replace function oshepherd.append(job oshepherd.jobs, sequence integer, content text, checksum text)
returns void language plpgsql set search_path = pg_catalog as $$
declare chunk oshepherd.chunks; size integer := pg_catalog.octet_length(content);
begin
  if size not between 1 and 65536 or checksum is distinct from pg_catalog.encode(extensions.digest(pg_catalog.convert_to(content,'UTF8'),'sha256'),'hex') then
    raise sqlstate 'PT422' using message = 'invalid_chunk';
  end if;
  select * into chunk from oshepherd.chunks where job_id = job.id and seq = sequence;
  if found then
    if chunk.attempt_id <> job.attempt_id or chunk.checksum <> checksum or chunk.content <> content then
      raise sqlstate 'PT409' using message = 'idempotency_conflict';
    end if;
    return;
  end if;
  if sequence <> job.last_seq + 1 then raise sqlstate 'PT409' using message = 'sequence_gap'; end if;
  if job.output_bytes + size > job.max_output_bytes then raise sqlstate 'PT413' using message = 'output_limit_exceeded'; end if;
  insert into oshepherd.chunks(job_id,seq,attempt_id,content,checksum,size_bytes) values(job.id,sequence,job.attempt_id,content,checksum,size);
  update oshepherd.jobs set last_seq = sequence, output_bytes = output_bytes + size where id = job.id;
end $$;
create or replace function oshepherd_api.append_output(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd.workers; job oshepherd.jobs; replay jsonb; sequence integer;
begin
  perform oshepherd.check_wire(p_request,array['session_id','generation','job_id','attempt_id','seq','content','checksum']);
  worker := oshepherd.lock_worker(p_request);
  replay := oshepherd.worker_replay(p_request,worker.id,'append_output');
  if replay is not null then return replay; end if;
  job := oshepherd.attempt(p_request,worker);
  if not job.stream then raise sqlstate 'PT409' using message = 'invalid_output_mode'; end if;
  if pg_catalog.jsonb_typeof(p_request->'content') is distinct from 'string' or pg_catalog.jsonb_typeof(p_request->'checksum') is distinct from 'string' then
    raise sqlstate 'PT422' using message = 'invalid_chunk';
  end if;
  sequence := oshepherd.integer_field(p_request,'seq',1,2147483647);
  perform oshepherd.append(job,sequence,p_request->>'content',p_request->>'checksum');
  return oshepherd.worker_finish(p_request,worker.id,'append_output',job.id,pg_catalog.jsonb_build_object('seq',sequence));
end $$;
create or replace function oshepherd_api.finish_job(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd.workers; job oshepherd.jobs; replay jsonb; chunk jsonb; result_descriptor jsonb; content text; terminal_record text; size integer;
begin
  perform oshepherd.check_wire(p_request,array['session_id','generation','job_id','attempt_id','final_chunks','result'],array['result']);
  worker := oshepherd.lock_worker(p_request);
  replay := oshepherd.worker_replay(p_request,worker.id,'finish_job');
  if replay is not null then return replay; end if;
  job := oshepherd.attempt(p_request,worker);
  if pg_catalog.jsonb_typeof(p_request->'final_chunks') is distinct from 'array' or pg_catalog.jsonb_array_length(p_request->'final_chunks') > 16 then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  if job.stream then
    if p_request->'result' <> 'null'::jsonb or pg_catalog.jsonb_array_length(p_request->'final_chunks') = 0 then
      raise sqlstate 'PT422' using message = 'invalid_output_mode';
    end if;
    select pg_catalog.string_agg(value #>> '{}','' order by position) into terminal_record
      from pg_catalog.jsonb_array_elements(p_request->'final_chunks') with ordinality as fragments(value,position);
    if pg_catalog.octet_length(terminal_record)>1048576 or right(terminal_record,1) <> E'\n'
      or pg_catalog.jsonb_typeof(terminal_record::jsonb) is distinct from 'object'
      or (terminal_record::jsonb)->'done' is distinct from 'true'::jsonb then
      raise sqlstate 'PT422' using message = 'invalid_terminal_record';
    end if;
    for chunk in select value from pg_catalog.jsonb_array_elements(p_request->'final_chunks') loop
      if pg_catalog.jsonb_typeof(chunk) <> 'string' then raise sqlstate 'PT422' using message = 'invalid_chunk'; end if;
      content := chunk #>> '{}';
      perform oshepherd.append(job,job.last_seq+1,content,pg_catalog.encode(extensions.digest(pg_catalog.convert_to(content,'UTF8'),'sha256'),'hex'));
      select * into job from oshepherd.jobs where id = job.id;
    end loop;
  else
    if pg_catalog.jsonb_array_length(p_request->'final_chunks') <> 0 then raise sqlstate 'PT422' using message = 'invalid_output_mode'; end if;
    result_descriptor := p_request->'result';
    perform oshepherd.check_fields(result_descriptor,array['inline','object_path','size_bytes','checksum','content_type'],array['inline','object_path']);
    if result_descriptor->>'content_type' <> 'application/json' or pg_catalog.jsonb_typeof(result_descriptor->'checksum') is distinct from 'string'
      or result_descriptor->>'checksum' !~ '^[a-f0-9]{64}$' then
      raise sqlstate 'PT422' using message = 'invalid_result';
    end if;
    size := oshepherd.integer_field(result_descriptor,'size_bytes',1,33554432);
    if size > job.max_output_bytes then raise sqlstate 'PT413' using message = 'output_too_large'; end if;
    if result_descriptor->'object_path' = 'null'::jsonb then
      if pg_catalog.jsonb_typeof(result_descriptor->'inline') is distinct from 'string' then raise sqlstate 'PT422' using message = 'invalid_result'; end if;
      content := result_descriptor->>'inline';
      if pg_catalog.octet_length(content) <> size or size > 262144
        or result_descriptor->>'checksum' is distinct from pg_catalog.encode(extensions.digest(pg_catalog.convert_to(content,'UTF8'),'sha256'),'hex') then
        raise sqlstate 'PT422' using message = 'invalid_result';
      end if;
      perform content::jsonb;
    else
      if result_descriptor->'inline' <> 'null'::jsonb or pg_catalog.jsonb_typeof(result_descriptor->'object_path') is distinct from 'string'
        or result_descriptor->>'object_path' <> job.cluster_id::text || '/' || job.id::text || '/' || job.attempt_id::text || '/result.json' then
        raise sqlstate 'PT422' using message = 'invalid_result';
      end if;
      insert into oshepherd.result_objects(job_id,bucket_id,object_path)
        values(job.id,'oshepherd-results',result_descriptor->>'object_path');
    end if;
    update oshepherd.jobs set result = result_descriptor, output_bytes = size where id = job.id;
  end if;
  perform oshepherd.terminal(job.id,'succeeded');
  return oshepherd.worker_finish(p_request,worker.id,'finish_job',job.id,'{"state":"succeeded"}');
exception when invalid_text_representation then raise sqlstate 'PT422' using message = 'invalid_result';
  when foreign_key_violation then raise sqlstate 'PT409' using message = 'result_object_missing';
end $$;
create or replace function oshepherd_api.fail_attempt(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd.workers; job oshepherd.jobs; replay jsonb;
begin
  perform oshepherd.check_wire(p_request,array['session_id','generation','job_id','attempt_id','error','retryable']);
  worker := oshepherd.lock_worker(p_request);
  replay := oshepherd.worker_replay(p_request,worker.id,'fail_attempt');
  if replay is not null then return replay; end if;
  job := oshepherd.attempt(p_request,worker);
  perform oshepherd.check_fields(p_request->'error',array['code','message']);
  if pg_catalog.jsonb_typeof(p_request->'retryable') is distinct from 'boolean'
    or pg_catalog.jsonb_typeof(p_request #> '{error,code}') is distinct from 'string'
    or p_request #>> '{error,code}' !~ '^[a-z][a-z0-9_]{0,63}$'
    or pg_catalog.jsonb_typeof(p_request #> '{error,message}') is distinct from 'string'
    or pg_catalog.length(p_request #>> '{error,message}') > 512 then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  perform oshepherd.retry_or_fail(job,(p_request->>'retryable')::boolean,p_request->'error');
  select * into job from oshepherd.jobs where id = job.id;
  return oshepherd.worker_finish(p_request,worker.id,'fail_attempt',job.id,oshepherd.job_wire(job));
end $$;

create or replace function oshepherd_api.read_updates(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare caller oshepherd.api_keys; cursor jsonb; job oshepherd.jobs; chunk oshepherd.chunks;
  jobs jsonb := '[]'; page jsonb; chunks jsonb; bytes_left integer; chunks_left integer; sequence integer; token uuid;
begin
  perform oshepherd.check_wire(p_request,array['cluster_id','key','cursors','max_bytes','max_chunks']);
  caller := oshepherd.caller(p_request);
  bytes_left := oshepherd.integer_field(p_request,'max_bytes',1,262144);
  chunks_left := oshepherd.integer_field(p_request,'max_chunks',1,64);
  if pg_catalog.jsonb_typeof(p_request->'cursors') is distinct from 'array' or pg_catalog.jsonb_array_length(p_request->'cursors') > 32 then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  perform oshepherd.reap(caller.cluster_id,128);
  for cursor in select value from pg_catalog.jsonb_array_elements(p_request->'cursors') loop
    perform oshepherd.check_fields(cursor,array['job_id','cursor','consumer_token'],array['consumer_token']);
    token := null;
    if cursor->'consumer_token' <> 'null'::jsonb then token := oshepherd.uuid_field(cursor,'consumer_token'); end if;
    sequence := oshepherd.integer_field(cursor,'cursor',0,2147483647);
    select * into job from oshepherd.jobs where id = oshepherd.uuid_field(cursor,'job_id') and key_id = caller.id for update;
    if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
    if job.mode = 'sync' and job.consumer_token is distinct from token then raise sqlstate 'PT403' using message = 'forbidden'; end if;
    if sequence > job.last_seq then raise sqlstate 'PT409' using message = 'invalid_cursor'; end if;
    if job.state in ('queued','running') and job.mode = 'sync' then
      update oshepherd.jobs set consumer_until = pg_catalog.clock_timestamp()+interval '60 seconds' where id = job.id;
    end if;
    chunks := '[]';
    if job.content_expires_at is null or job.content_expires_at > pg_catalog.clock_timestamp() then
      for chunk in select * from oshepherd.chunks where job_id = job.id and seq > sequence order by seq limit chunks_left loop
        if chunk.size_bytes > bytes_left then exit; end if;
        bytes_left := bytes_left - chunk.size_bytes; chunks_left := chunks_left - 1; sequence := chunk.seq;
        chunks := chunks || pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('seq',chunk.seq,'content',chunk.content,'checksum',chunk.checksum));
      end loop;
    end if;
    page := oshepherd.job_wire(job) || pg_catalog.jsonb_build_object('chunks',chunks,'cursor',sequence,
      'result',case when job.content_expires_at > pg_catalog.clock_timestamp() and (job.result->>'object_path' is not null or job.output_bytes <= bytes_left) then job.result else null end,
      'result_omitted',job.result is not null and job.result->>'object_path' is null and job.content_expires_at > pg_catalog.clock_timestamp() and job.output_bytes > bytes_left);
    if job.result is not null and job.result->>'object_path' is null and job.content_expires_at > pg_catalog.clock_timestamp() and job.output_bytes <= bytes_left then
      bytes_left := bytes_left-job.output_bytes;
    end if;
    jobs := jobs || pg_catalog.jsonb_build_array(page);
  end loop;
  return oshepherd.answer(pg_catalog.jsonb_build_object('jobs',jobs,'server_now',oshepherd.timestamp_wire(pg_catalog.clock_timestamp())));
end $$;
create or replace function oshepherd_api.cancel_job(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare caller oshepherd.api_keys; job oshepherd.jobs; token uuid;
begin
  perform oshepherd.check_wire(p_request,array['cluster_id','key','job_id','consumer_token','reason'],array['consumer_token']);
  caller := oshepherd.caller(p_request);
  if p_request->>'reason' not in ('cancelled','execution_expired','consumer_expired','reader_stalled') then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  select * into job from oshepherd.jobs where id = oshepherd.uuid_field(p_request,'job_id') and key_id = caller.id for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  if p_request->'consumer_token' <> 'null'::jsonb then token := oshepherd.uuid_field(p_request,'consumer_token'); end if;
  if job.mode = 'sync' and job.consumer_token is distinct from token then raise sqlstate 'PT403' using message = 'forbidden'; end if;
  perform oshepherd.terminal(job.id,case when p_request->>'reason' = 'execution_expired' then 'expired' else 'cancelled' end,
    pg_catalog.jsonb_build_object('code',p_request->>'reason','message','Job cancelled'));
  select * into job from oshepherd.jobs where id = job.id;
  return oshepherd.answer(oshepherd.job_wire(job));
end $$;
create or replace function oshepherd_api.backend_info(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare caller oshepherd.api_keys;
begin
  perform oshepherd.check_wire(p_request,array['cluster_id','key']); caller := oshepherd.caller(p_request);
  return oshepherd.answer(pg_catalog.jsonb_build_object('protocol',1,'schema','oshepherd_api','experimental',true,
    'cluster_id',caller.cluster_id,'server_now',oshepherd.timestamp_wire(pg_catalog.clock_timestamp())));
end $$;
create or replace function oshepherd_api.read_metadata(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare caller oshepherd.api_keys; response jsonb; model_name text; digest_count integer;
begin
  perform oshepherd.check_wire(p_request,array['cluster_id','key','operation','model'],array['model']); caller := oshepherd.caller(p_request);
  if p_request->>'operation' = 'tags' then
    select pg_catalog.jsonb_build_object('models',coalesce(pg_catalog.jsonb_agg(details order by name),'[]')) into response from (
      select distinct on (m.name) m.name,m.details from oshepherd.worker_models m join oshepherd.workers w on w.id = m.worker_id
        where w.cluster_id = caller.cluster_id and w.revoked_at is null and not w.draining and w.heartbeat_at > pg_catalog.clock_timestamp()-interval '60 seconds'
        and m.observed_at > pg_catalog.clock_timestamp()-interval '60 seconds' order by m.name,w.id) models;
  elsif p_request->>'operation' = 'ps' then
    select pg_catalog.jsonb_build_object('models',coalesce(pg_catalog.jsonb_agg(model),'[]')) into response from (
      select distinct model from oshepherd.workers w cross join lateral pg_catalog.jsonb_array_elements(coalesce(w.running->'models','[]')) model
        where w.cluster_id = caller.cluster_id and w.revoked_at is null and w.heartbeat_at > pg_catalog.clock_timestamp()-interval '60 seconds') models;
  elsif p_request->>'operation' = 'version' then
    select w.version into response from oshepherd.workers w where w.cluster_id = caller.cluster_id and w.revoked_at is null
      and w.heartbeat_at > pg_catalog.clock_timestamp()-interval '60 seconds' order by w.id limit 1;
    if response is null then response := '{}'; end if;
  elsif p_request->>'operation' = 'show' then
    if pg_catalog.jsonb_typeof(p_request->'model') is distinct from 'string' then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
    model_name := p_request->>'model'; if pg_catalog.strpos(model_name,':') = 0 then model_name := model_name || ':latest'; end if;
    select count(distinct m.digest) into digest_count from oshepherd.worker_models m join oshepherd.workers w on w.id = m.worker_id
      where w.cluster_id = caller.cluster_id and w.revoked_at is null and w.heartbeat_at > pg_catalog.clock_timestamp()-interval '60 seconds'
        and m.observed_at > pg_catalog.clock_timestamp()-interval '60 seconds' and m.name = model_name;
    if digest_count = 0 then raise sqlstate 'PT404' using message = 'model_not_found'; end if;
    if digest_count > 1 then raise sqlstate 'PT409' using message = 'model_digest_conflict'; end if;
    select m.show into response from oshepherd.worker_models m join oshepherd.workers w on w.id = m.worker_id
      where w.cluster_id = caller.cluster_id and w.revoked_at is null and w.heartbeat_at > pg_catalog.clock_timestamp()-interval '60 seconds'
        and m.observed_at > pg_catalog.clock_timestamp()-interval '60 seconds' and m.name = model_name order by w.id limit 1;
  else raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  return oshepherd.answer(pg_catalog.jsonb_build_object('response',response));
end $$;
create or replace function oshepherd_api.reap_jobs(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare cluster uuid;
begin
  perform oshepherd.check_wire(p_request,array['cluster_id','limit']); perform oshepherd.require_service();
  cluster := oshepherd.uuid_field(p_request,'cluster_id');
  perform 1 from oshepherd.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  return oshepherd.answer(pg_catalog.jsonb_build_object('reaped',oshepherd.reap(cluster,oshepherd.integer_field(p_request,'limit',1,256))));
end $$;
create or replace function oshepherd.purge(cluster uuid,batch_limit integer)
returns jsonb language plpgsql set search_path = pg_catalog as $$
declare job oshepherd.jobs; purged integer := 0; paths jsonb := '[]';
begin
  if batch_limit not between 1 and 256 then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  -- Caller holds the cluster lock. Cap chunk and receipt rows as well as jobs;
  -- a large expired stream cannot make one purge statement unbounded.
  for job in select * from oshepherd.jobs where cluster_id = cluster and content_expires_at <= pg_catalog.clock_timestamp()
    and (request <> '{}'::jsonb or result is not null or tombstone_expires_at <= pg_catalog.clock_timestamp()) order by id
    limit batch_limit for update loop
    delete from oshepherd.chunks where job_id = job.id and seq in(
      select seq from oshepherd.chunks where job_id = job.id order by seq limit 64);
    if exists(select 1 from oshepherd.chunks where job_id = job.id) then continue; end if;
    if job.tombstone_expires_at <= pg_catalog.clock_timestamp() then
      delete from oshepherd.worker_operations where (worker_id,operation_id) in(
        select worker_id,operation_id from oshepherd.worker_operations where job_id = job.id order by worker_id,operation_id limit 64);
      if exists(select 1 from oshepherd.worker_operations where job_id = job.id) then continue; end if;
    end if;
    if job.result->>'object_path' is not null then
      insert into oshepherd.result_deletions(cluster_id,object_path) values(cluster,job.result->>'object_path') on conflict(object_path) do nothing;
      paths := paths || pg_catalog.jsonb_build_array(job.result->>'object_path');
    end if;
    delete from oshepherd.result_objects where job_id = job.id;
    update oshepherd.jobs set request = '{}', result = null where id = job.id;
    if job.tombstone_expires_at <= pg_catalog.clock_timestamp() then delete from oshepherd.jobs where id = job.id; end if;
    purged := purged + 1;
  end loop;
  delete from oshepherd.worker_operations where (worker_id,operation_id) in(
    select o.worker_id,o.operation_id from oshepherd.worker_operations o join oshepherd.workers w on w.id = o.worker_id
      where w.cluster_id = cluster and o.created_at < pg_catalog.clock_timestamp()-interval '2 days'
      order by o.created_at,o.worker_id,o.operation_id limit batch_limit);
  return pg_catalog.jsonb_build_object('purged',purged,'object_paths',paths);
end $$;
create or replace function oshepherd_api.purge_expired(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare cluster uuid;
begin
  perform oshepherd.check_wire(p_request,array['cluster_id','limit']); perform oshepherd.require_service();
  cluster := oshepherd.uuid_field(p_request,'cluster_id');
  perform 1 from oshepherd.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  return oshepherd.answer(oshepherd.purge(cluster,oshepherd.integer_field(p_request,'limit',1,256)));
end $$;

reset role;
revoke all on all tables in schema oshepherd from public, anon, authenticated, service_role;
revoke all on all functions in schema oshepherd from public, anon, authenticated, service_role;
revoke all on all functions in schema oshepherd_api from public, anon, authenticated, service_role;
grant execute on function oshepherd_api.bootstrap_cluster(jsonb),oshepherd_api.create_api_key(jsonb),
  oshepherd_api.enroll_worker(jsonb),oshepherd_api.revoke_worker(jsonb),oshepherd_api.submit_job(jsonb),
  oshepherd_api.read_updates(jsonb),oshepherd_api.cancel_job(jsonb),oshepherd_api.read_metadata(jsonb),
  oshepherd_api.backend_info(jsonb),oshepherd_api.reap_jobs(jsonb),oshepherd_api.purge_expired(jsonb) to service_role;
grant execute on function oshepherd_api.register_worker(jsonb),oshepherd_api.update_models(jsonb),
  oshepherd_api.heartbeat_and_claim(jsonb),oshepherd_api.append_output(jsonb),oshepherd_api.finish_job(jsonb),
  oshepherd_api.fail_attempt(jsonb) to authenticated;
notify pgrst, 'reload schema';

select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_schema_v1',0));
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
  values('oshepherd-results','oshepherd-results',false,33554432,array['application/json'])
  on conflict(id) do nothing;
create table if not exists oshepherd.result_objects (
  job_id uuid primary key references oshepherd.jobs(id) on delete cascade,
  bucket_id text not null check (bucket_id = 'oshepherd-results'),
  object_path text not null
);
alter table oshepherd.result_objects owner to oshepherd_runtime;
set local role oshepherd_runtime;
alter table oshepherd.result_objects enable row level security;
revoke all on oshepherd.result_objects from public,anon,authenticated,service_role;
create or replace function oshepherd.can_upload_result(object_path text)
returns boolean language sql stable security definer set search_path = pg_catalog as $$
  select oshepherd.jwt_role() = 'authenticated' and exists(
    select 1 from oshepherd.jobs j join oshepherd.workers w on w.id = j.worker_id
      join oshepherd.memberships m on m.cluster_id = w.cluster_id and m.auth_subject = w.auth_subject
    where w.auth_subject = oshepherd.jwt_subject() and w.revoked_at is null and m.revoked_at is null and m.role = 'worker'
      and j.generation = w.generation and j.session_id = w.session_id and j.state = 'running' and not j.stream
      and j.lease_until > pg_catalog.clock_timestamp() and j.execution_deadline > pg_catalog.clock_timestamp()
      and (j.mode = 'async' or j.consumer_until > pg_catalog.clock_timestamp())
      and object_path = j.cluster_id::text || '/' || j.id::text || '/' || j.attempt_id::text || '/result.json'
  )
$$;
grant usage on schema oshepherd to authenticated;
revoke all on function oshepherd.can_upload_result(text) from public,anon,service_role;
grant execute on function oshepherd.can_upload_result(text) to authenticated;
reset role;
drop policy if exists oshepherd_result_insert on storage.objects;
create policy oshepherd_result_insert on storage.objects for insert to authenticated
  with check(bucket_id = 'oshepherd-results' and oshepherd.can_upload_result(name));
drop policy if exists oshepherd_result_verify on storage.objects;
create policy oshepherd_result_verify on storage.objects for select to authenticated
  using(bucket_id = 'oshepherd-results' and oshepherd.can_upload_result(name));
notify pgrst, 'reload schema';

select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_schema_v1',0));
set local role oshepherd_runtime;
create table if not exists oshepherd.maintenance_cursors (
  cluster_id uuid primary key references oshepherd.clusters(id) on delete cascade,
  object_cursor text not null default ''
);
create table if not exists oshepherd.maintenance_operations (
  operation_id uuid primary key,
  cluster_id uuid not null references oshepherd.clusters(id) on delete cascade,
  kind text not null,
  request_hash bytea not null,
  response jsonb not null,
  expires_at timestamptz not null default pg_catalog.clock_timestamp()+interval '2 days'
);
create index if not exists maintenance_operations_age on oshepherd.maintenance_operations(cluster_id,expires_at,operation_id);
alter table oshepherd.maintenance_cursors enable row level security;
alter table oshepherd.maintenance_operations enable row level security;
revoke all on oshepherd.maintenance_cursors,oshepherd.maintenance_operations from public,anon,authenticated,service_role;
reset role;

create or replace function oshepherd_api.maintenance_tick(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare cluster uuid; batch_limit integer; operation uuid; receipt oshepherd.maintenance_operations;
  fingerprint bytea; response jsonb; purged jsonb; reaped integer; enqueued integer := 0;
  candidate record; scanned integer := 0; cursor_path text; paths jsonb := '[]'; deletion record;
begin
  perform oshepherd.check_wire(p_request,array['cluster_id','limit']);
  perform oshepherd.require_service();
  cluster := oshepherd.uuid_field(p_request,'cluster_id');
  batch_limit := oshepherd.integer_field(p_request,'limit',1,64);
  operation := oshepherd.uuid_field(p_request,'operation_id');
  fingerprint := extensions.digest(pg_catalog.convert_to((p_request-'operation_id')::text,'UTF8'),'sha256');
  perform 1 from oshepherd.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  select * into receipt from oshepherd.maintenance_operations where operation_id = operation;
  if found then
    if receipt.cluster_id <> cluster or receipt.kind <> 'maintenance_tick' or receipt.request_hash <> fingerprint then
      raise sqlstate 'PT409' using message = 'idempotency_conflict';
    end if;
    return receipt.response;
  end if;
  reaped := oshepherd.reap(cluster,batch_limit);
  purged := oshepherd.purge(cluster,batch_limit);
  insert into oshepherd.maintenance_cursors(cluster_id) values(cluster) on conflict do nothing;
  select object_cursor into cursor_path from oshepherd.maintenance_cursors where cluster_id = cluster for update;
  -- Keyset scan, including young/referenced entries: inspect at most one page,
  -- never scan an unbounded bucket looking for a page of eligible orphans.
  -- Storage's (bucket_id,name COLLATE "C") index supports this bytewise
  -- namespace range. Locale ordering ignores punctuation and breaks it.
  for candidate in select name,created_at from storage.objects where bucket_id = 'oshepherd-results'
    and name collate "C" >= (cluster::text || '/') collate "C"
    and name collate "C" < (cluster::text || '0') collate "C"
    and name collate "C" > cursor_path collate "C"
    order by name collate "C" limit batch_limit loop
    scanned := scanned+1; cursor_path := candidate.name;
    if candidate.created_at > pg_catalog.clock_timestamp()-interval '10 minutes'
      or candidate.name !~ '^([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/){3}result[.]json$'
      or exists(select 1 from oshepherd.result_objects where object_path = candidate.name) then continue; end if;
    if exists(select 1 from oshepherd.jobs j where j.cluster_id = cluster
        and j.id = pg_catalog.split_part(candidate.name,'/',2)::uuid
        and j.attempt_id = pg_catalog.split_part(candidate.name,'/',3)::uuid
        and j.state = 'running' and j.lease_until > pg_catalog.clock_timestamp()
        and j.execution_deadline > pg_catalog.clock_timestamp()
        and (j.mode = 'async' or j.consumer_until > pg_catalog.clock_timestamp())) then continue; end if;
    insert into oshepherd.result_deletions(cluster_id,object_path)
      values(cluster,candidate.name) on conflict(object_path) do nothing;
    if found then enqueued := enqueued+1; end if;
  end loop;
  update oshepherd.maintenance_cursors set object_cursor = case when scanned < batch_limit then '' else cursor_path end where cluster_id = cluster;
  for deletion in select d.object_path from oshepherd.result_deletions d where d.cluster_id = cluster
    and d.available_at <= pg_catalog.clock_timestamp() and (d.leased_until is null or d.leased_until <= pg_catalog.clock_timestamp())
    and not exists(select 1 from oshepherd.result_objects r where r.object_path = d.object_path)
    order by d.available_at,d.object_path limit batch_limit for update of d skip locked loop
    update oshepherd.result_deletions set claim_id = operation,leased_until = pg_catalog.clock_timestamp()+interval '45 seconds',
      attempts = least(attempts+1,32) where object_path = deletion.object_path;
    paths := paths || pg_catalog.jsonb_build_array(deletion.object_path);
  end loop;
  delete from oshepherd.maintenance_operations where operation_id in(
    select operation_id from oshepherd.maintenance_operations where cluster_id = cluster and expires_at <= pg_catalog.clock_timestamp()
      order by expires_at,operation_id limit batch_limit);
  response := oshepherd.answer(pg_catalog.jsonb_build_object('reaped',reaped,'purged',purged->'purged',
    'scanned',scanned,'enqueued',enqueued,'claim_id',operation,'object_paths',paths));
  insert into oshepherd.maintenance_operations(operation_id,cluster_id,kind,request_hash,response)
    values(operation,cluster,'maintenance_tick',fingerprint,response);
  return response;
exception when unique_violation then raise sqlstate 'PT409' using message = 'idempotency_conflict';
end $$;

create or replace function oshepherd_api.complete_maintenance(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare cluster uuid; claim uuid; operation uuid; receipt oshepherd.maintenance_operations;
  fingerprint bytea; response jsonb; path record; deletion oshepherd.result_deletions;
  deleted integer := 0; retrying integer := 0;
begin
  perform oshepherd.check_wire(p_request,array['cluster_id','claim_id','deleted_paths','failed_paths']);
  perform oshepherd.require_service();
  cluster := oshepherd.uuid_field(p_request,'cluster_id');
  claim := oshepherd.uuid_field(p_request,'claim_id');
  operation := oshepherd.uuid_field(p_request,'operation_id');
  if pg_catalog.jsonb_typeof(p_request->'deleted_paths') is distinct from 'array'
    or pg_catalog.jsonb_typeof(p_request->'failed_paths') is distinct from 'array'
    or pg_catalog.jsonb_array_length(p_request->'deleted_paths')+pg_catalog.jsonb_array_length(p_request->'failed_paths') > 64 then
    raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  if exists(select 1 from pg_catalog.jsonb_array_elements((p_request->'deleted_paths')||(p_request->'failed_paths')) v
    where pg_catalog.jsonb_typeof(v) <> 'string' or pg_catalog.left(v#>>'{}',37) <> cluster::text || '/'
      or v#>>'{}' !~ '^([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/){3}result[.]json$')
    or (select count(*) <> count(distinct v) from pg_catalog.jsonb_array_elements((p_request->'deleted_paths')||(p_request->'failed_paths')) v) then
    raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  perform 1 from oshepherd.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  fingerprint := extensions.digest(pg_catalog.convert_to((p_request-'operation_id')::text,'UTF8'),'sha256');
  select * into receipt from oshepherd.maintenance_operations where operation_id = operation;
  if found then
    if receipt.cluster_id <> cluster or receipt.kind <> 'complete_maintenance' or receipt.request_hash <> fingerprint then
      raise sqlstate 'PT409' using message = 'idempotency_conflict'; end if;
    return receipt.response;
  end if;
  for path in select value as name,true as reported_deleted from pg_catalog.jsonb_array_elements_text(p_request->'deleted_paths')
    union all select value as name,false as reported_deleted from pg_catalog.jsonb_array_elements_text(p_request->'failed_paths') loop
    select * into deletion from oshepherd.result_deletions where cluster_id = cluster and object_path = path.name for update;
    if not found then continue; end if;
    if deletion.claim_id is distinct from claim then raise sqlstate 'PT409' using message = 'stale_cleanup'; end if;
    -- HTTP 200 alone does not prove object deletion. Never forget intent while
    -- Storage metadata still exists (including partial/ambiguous deletions).
    if path.reported_deleted and not exists(select 1 from storage.objects where bucket_id = 'oshepherd-results' and name = path.name) then
      delete from oshepherd.result_deletions where object_path = path.name;
      deleted := deleted+1;
    else
      update oshepherd.result_deletions set claim_id = null,leased_until = null,
        available_at = pg_catalog.clock_timestamp()+pg_catalog.make_interval(secs => least(300,pg_catalog.power(2,least(attempts,8))::integer))
        where object_path = path.name;
      retrying := retrying+1;
    end if;
  end loop;
  response := oshepherd.answer(pg_catalog.jsonb_build_object('deleted',deleted,'retrying',retrying));
  insert into oshepherd.maintenance_operations(operation_id,cluster_id,kind,request_hash,response)
    values(operation,cluster,'complete_maintenance',fingerprint,response);
  return response;
exception when unique_violation then raise sqlstate 'PT409' using message = 'idempotency_conflict';
end $$;

revoke all on function oshepherd_api.maintenance_tick(jsonb),oshepherd_api.complete_maintenance(jsonb) from public,anon,authenticated,service_role;
grant execute on function oshepherd_api.maintenance_tick(jsonb),oshepherd_api.complete_maintenance(jsonb) to service_role;
notify pgrst,'reload schema';

select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_schema_v1',0));
set local role oshepherd_runtime;
create table if not exists oshepherd.sweep_cursors (
  kind text primary key check (kind in ('reap','purge')),
  cluster_cursor uuid,
  last_run_at timestamptz
);
alter table oshepherd.sweep_cursors enable row level security;
revoke all on oshepherd.sweep_cursors from public,anon,authenticated,service_role;

create or replace function oshepherd.scheduled_sweep(kind text)
returns integer language plpgsql security definer set search_path = pg_catalog
  set statement_timeout = '5s' set lock_timeout = '100ms' as $$
declare cursor_id uuid; cluster record; visited integer := 0;
begin
  if scheduled_sweep.kind not in ('reap','purge') or scheduled_sweep.kind is null then
    raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  if not pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_sweep_'||scheduled_sweep.kind,0)) then return 0; end if;
  insert into oshepherd.sweep_cursors(kind) values(scheduled_sweep.kind) on conflict do nothing;
  select cluster_cursor into cursor_id from oshepherd.sweep_cursors s where s.kind = scheduled_sweep.kind for update;
  -- A keyset page bounds cluster work; locked clusters are retried on the next
  -- round rather than delaying unrelated admission/heartbeat transactions.
  for cluster in select id from oshepherd.clusters
      where cursor_id is null or id > cursor_id order by id limit 4 for update skip locked loop
    if scheduled_sweep.kind = 'reap' then perform oshepherd.reap(cluster.id,8);
    else perform oshepherd.purge(cluster.id,8); end if;
    visited := visited+1; cursor_id := cluster.id;
  end loop;
  update oshepherd.sweep_cursors s set cluster_cursor = case when visited < 4 then null else cursor_id end,
    last_run_at = pg_catalog.clock_timestamp() where s.kind = scheduled_sweep.kind;
  return visited;
end $$;
revoke all on function oshepherd.scheduled_sweep(text) from public,anon,authenticated,service_role;
reset role;

reset role;
create table oshepherd.release_migrations(
  version text primary key,
  sha256 text not null check(sha256 ~ '^[0-9a-f]{64}$'),
  fingerprint text not null,
  installed_at timestamptz not null default pg_catalog.clock_timestamp()
);
alter table oshepherd.release_migrations owner to oshepherd_runtime;
alter table oshepherd.release_migrations enable row level security;
revoke all on oshepherd.release_migrations from public,anon,authenticated,service_role;
-- Merge an existing PostgREST role setting; retain all unrelated schemas.
do $exposure$ declare schemas text; begin
  select pg_catalog.substr(setting,18) into schemas from pg_catalog.pg_db_role_setting s
    cross join lateral pg_catalog.unnest(s.setconfig) setting
    where s.setrole = (select oid from pg_catalog.pg_roles where rolname='authenticator')
      and s.setdatabase = 0 and setting like 'pgrst.db_schemas=%';
  schemas := coalesce(schemas, pg_catalog.current_setting('pgrst.db_schemas',true), 'public,graphql_public');
  if not 'oshepherd_api' = any(pg_catalog.regexp_split_to_array(schemas,'\s*,\s*')) then
    schemas := schemas || ',oshepherd_api';
  end if;
  execute pg_catalog.format('alter role authenticator set pgrst.db_schemas = %L',schemas);
end $exposure$;
notify pgrst,'reload config';
notify pgrst,'reload schema';

-- Included once in the initial immutable release migration.
set local role oshepherd_runtime;
create function oshepherd.schema_fingerprint()
returns text language sql stable security definer set search_path=pg_catalog as $$
 select pg_catalog.encode(extensions.digest(pg_catalog.convert_to(pg_catalog.jsonb_build_object(
  'functions',(select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(n.nspname,p.proname,
     pg_catalog.pg_get_functiondef(p.oid),p.proacl,p.proowner) order by n.nspname,p.proname,p.oid)
    from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid=p.pronamespace where n.nspname in('oshepherd','oshepherd_api')),
  'relations',(select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(n.nspname,c.relname,c.relkind,c.relowner,
     c.relacl,c.relrowsecurity,c.relforcerowsecurity) order by n.nspname,c.relname)
    from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace where n.nspname in('oshepherd','oshepherd_api')),
  'columns',(select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(c.relname,a.attname,
     pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_catalog.pg_get_expr(d.adbin,d.adrelid)) order by c.relname,a.attnum)
    from pg_catalog.pg_attribute a join pg_catalog.pg_class c on c.oid=a.attrelid join pg_catalog.pg_namespace n on n.oid=c.relnamespace
    left join pg_catalog.pg_attrdef d on d.adrelid=a.attrelid and d.adnum=a.attnum
    where n.nspname='oshepherd' and a.attnum>0 and not a.attisdropped),
  'constraints',(select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(c.conname,pg_catalog.pg_get_constraintdef(c.oid)) order by c.conname,c.oid)
    from pg_catalog.pg_constraint c join pg_catalog.pg_namespace n on n.oid=c.connamespace where n.nspname='oshepherd'),
  'indexes',(select pg_catalog.jsonb_agg(pg_catalog.pg_get_indexdef(i.indexrelid) order by c.relname)
    from pg_catalog.pg_index i join pg_catalog.pg_class c on c.oid=i.indexrelid join pg_catalog.pg_namespace n on n.oid=c.relnamespace where n.nspname='oshepherd'),
  'schemas',(select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(nspname,nspowner,nspacl) order by nspname)
    from pg_catalog.pg_namespace where nspname in('oshepherd','oshepherd_api')),
  'policies',(select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(p.polname,p.polcmd,p.polroles,
     pg_catalog.pg_get_expr(p.polqual,p.polrelid),pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)) order by p.polname)
    from pg_catalog.pg_policy p join pg_catalog.pg_class c on c.oid=p.polrelid join pg_catalog.pg_namespace n on n.oid=c.relnamespace
    where n.nspname='oshepherd' or p.polname in('oshepherd_result_insert','oshepherd_result_verify')),
  'role',(select pg_catalog.jsonb_build_array(rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls)
    from pg_catalog.pg_roles where rolname='oshepherd_runtime'),
  'role_members',(select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(m.member,m.admin_option) order by m.member)
    from pg_catalog.pg_auth_members m join pg_catalog.pg_roles r on r.oid=m.roleid where r.rolname='oshepherd_runtime')
 )::text,'UTF8'),'sha256'),'hex')
$$;
revoke all on function oshepherd.schema_fingerprint() from public,anon,authenticated,service_role;
reset role;

-- Operator RPCs are server-only; no prompt, result or key hashes are returned.
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_release_v1',0));
set local role oshepherd_runtime;

create function oshepherd_api.installation_info(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare cluster uuid; info oshepherd.clusters; release jsonb;
begin
  perform oshepherd.check_wire(p_request,array['cluster_id']);
  perform oshepherd.require_service();
  cluster := oshepherd.uuid_field(p_request,'cluster_id');
  select * into info from oshepherd.clusters where id=cluster;
  if not found then raise sqlstate 'PT404' using message='not_found'; end if;
  select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('version',version,'sha256',sha256) order by version)
    into release from oshepherd.release_migrations;
  return oshepherd.answer(pg_catalog.jsonb_build_object(
    'cluster_id',cluster,'owner_subject',info.owner_subject,'enabled',info.enabled,'protocol',1,
    'migrations',release,
    'schema_matches', (select fingerprint=oshepherd.schema_fingerprint() from oshepherd.release_migrations order by version desc limit 1),
    'online_workers',(select count(*) from oshepherd.workers where cluster_id=cluster and revoked_at is null
      and not draining and heartbeat_at>pg_catalog.clock_timestamp()-interval '60 seconds'),
    'active_keys',(select count(*) from oshepherd.api_keys where cluster_id=cluster and revoked_at is null),
    'active_jobs',(select count(*) from oshepherd.jobs where cluster_id=cluster and state in('queued','running')),
    'workers',coalesce((select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('worker_id',w.id,'capacity',w.capacity,
      'revoked',w.revoked_at is not null,'draining',w.draining,'heartbeat_at',oshepherd.timestamp_wire(w.heartbeat_at)) order by w.id)
      from (select * from oshepherd.workers where cluster_id=cluster order by id limit 64) w),'[]'::jsonb)));
end $$;

create function oshepherd_api.revoke_api_key(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare replay jsonb; cluster uuid; key_id uuid;
begin
  perform oshepherd.check_request(p_request,array['cluster_id','key_id']);
  replay := oshepherd.admin_replay(p_request,'revoke_api_key');
  if replay is not null then return replay; end if;
  cluster:=oshepherd.uuid_field(p_request,'cluster_id'); key_id:=oshepherd.uuid_field(p_request,'key_id');
  perform 1 from oshepherd.clusters where id=cluster for update;
  if not found then raise sqlstate 'PT404' using message='not_found'; end if;
  update oshepherd.api_keys set revoked_at=coalesce(revoked_at,pg_catalog.clock_timestamp()) where id=key_id and cluster_id=cluster;
  if not found then raise sqlstate 'PT404' using message='not_found'; end if;
  return oshepherd.admin_finish(p_request,'revoke_api_key',pg_catalog.jsonb_build_object('key_id',key_id,'revoked',true));
end $$;

create function oshepherd_api.disable_cluster(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare replay jsonb; cluster uuid; job record;
begin
  perform oshepherd.check_request(p_request,array['cluster_id']);
  replay := oshepherd.admin_replay(p_request,'disable_cluster');
  if replay is not null then return replay; end if;
  cluster:=oshepherd.uuid_field(p_request,'cluster_id');
  perform 1 from oshepherd.clusters where id=cluster for update;
  if not found then raise sqlstate 'PT404' using message='not_found'; end if;
  update oshepherd.clusters set enabled=false where id=cluster;
  update oshepherd.api_keys set revoked_at=coalesce(revoked_at,pg_catalog.clock_timestamp()) where cluster_id=cluster;
  update oshepherd.workers set revoked_at=coalesce(revoked_at,pg_catalog.clock_timestamp()),draining=true where cluster_id=cluster;
  update oshepherd.memberships set revoked_at=coalesce(revoked_at,pg_catalog.clock_timestamp()) where cluster_id=cluster;
  -- Admission caps active jobs at 128, so this entire shutdown is bounded.
  for job in select id from oshepherd.jobs where cluster_id=cluster and state in('queued','running') for update loop
    perform oshepherd.terminal(job.id,'cancelled','{"code":"cluster_disabled","message":"Installation disabled"}'::jsonb);
  end loop;
  return oshepherd.admin_finish(p_request,'disable_cluster',pg_catalog.jsonb_build_object('cluster_id',cluster,'disabled',true));
end $$;

revoke all on function oshepherd_api.installation_info(jsonb),oshepherd_api.revoke_api_key(jsonb),oshepherd_api.disable_cluster(jsonb)
  from public,anon,authenticated;
grant execute on function oshepherd_api.installation_info(jsonb),oshepherd_api.revoke_api_key(jsonb),oshepherd_api.disable_cluster(jsonb)
  to service_role;
reset role;
notify pgrst,'reload schema';

-- Release record. Hash covers the bytes preceding this marker.
insert into oshepherd.release_migrations(version,sha256,fingerprint) values('20261011000100','dccbbf30f280f19bb297c7df3bfc66e70244999e6ab205707de52cd1b923ef75',oshepherd.schema_fingerprint());
