-- Isolated feasibility-spike SQL. Never apply to production migration history.
-- Only the dedicated oshepherd-development local stack is used by its harness.
begin;
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_spike_schema_v1', 0));

do $$ begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'oshepherd_spike_runtime') then
    create role oshepherd_spike_runtime nologin nobypassrls;
  end if;
end $$;
-- Supabase's postgres role is not a superuser. Its migration identity needs
-- explicit membership before it can assign schemas/functions to this owner.
grant oshepherd_spike_runtime to postgres;
create schema if not exists oshepherd_spike authorization oshepherd_spike_runtime;
create schema if not exists oshepherd_spike_api authorization oshepherd_spike_runtime;
grant usage on schema extensions to oshepherd_spike_runtime;
grant usage on schema oshepherd_spike_api to authenticated, service_role;

create table if not exists oshepherd_spike.clusters (
  id uuid primary key,
  owner_subject uuid not null references auth.users(id),
  enabled boolean not null default true,
  max_queued integer not null default 64 check (max_queued between 1 and 128),
  max_running integer not null default 64 check (max_running between 1 and 128),
  max_active integer not null default 128 check (max_active between 1 and 128),
  caller_max_active integer not null default 4 check (caller_max_active between 1 and 128),
  created_at timestamptz not null default pg_catalog.clock_timestamp()
);
create table if not exists oshepherd_spike.memberships (
  cluster_id uuid not null references oshepherd_spike.clusters(id),
  auth_subject uuid not null references auth.users(id),
  role text not null check (role in ('operator', 'worker')),
  revoked_at timestamptz,
  primary key (cluster_id, auth_subject)
);
create table if not exists oshepherd_spike.api_keys (
  id uuid primary key,
  cluster_id uuid not null references oshepherd_spike.clusters(id),
  key_hash bytea not null unique check (pg_catalog.octet_length(key_hash) = 32),
  revoked_at timestamptz,
  created_at timestamptz not null default pg_catalog.clock_timestamp()
);
create table if not exists oshepherd_spike.workers (
  id uuid primary key default pg_catalog.gen_random_uuid(),
  auth_subject uuid not null unique references auth.users(id),
  cluster_id uuid not null references oshepherd_spike.clusters(id),
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
create table if not exists oshepherd_spike.worker_sessions (
  worker_id uuid not null references oshepherd_spike.workers(id),
  operation_id uuid not null,
  generation integer not null,
  session_id uuid not null,
  primary key (worker_id, operation_id)
);
create table if not exists oshepherd_spike.worker_models (
  worker_id uuid not null references oshepherd_spike.workers(id),
  name text not null,
  digest text not null,
  operations jsonb not null,
  details jsonb not null,
  show jsonb not null,
  observed_at timestamptz not null default pg_catalog.clock_timestamp(),
  primary key (worker_id, name)
);
create table if not exists oshepherd_spike.admin_operations (
  operation_id uuid primary key,
  kind text not null,
  request_hash bytea not null,
  response jsonb not null
);

-- Only the migration identity can create foreign keys into Supabase-owned Auth
-- tables. Transfer our tables after creation; the runtime owner receives no
-- Auth table access, role administration, or login capability.
alter table oshepherd_spike.clusters owner to oshepherd_spike_runtime;
alter table oshepherd_spike.memberships owner to oshepherd_spike_runtime;
alter table oshepherd_spike.api_keys owner to oshepherd_spike_runtime;
alter table oshepherd_spike.workers owner to oshepherd_spike_runtime;
alter table oshepherd_spike.worker_sessions owner to oshepherd_spike_runtime;
alter table oshepherd_spike.worker_models owner to oshepherd_spike_runtime;
alter table oshepherd_spike.admin_operations owner to oshepherd_spike_runtime;
set local role oshepherd_spike_runtime;

alter table oshepherd_spike.clusters enable row level security;
alter table oshepherd_spike.memberships enable row level security;
alter table oshepherd_spike.api_keys enable row level security;
alter table oshepherd_spike.workers enable row level security;
alter table oshepherd_spike.worker_sessions enable row level security;
alter table oshepherd_spike.worker_models enable row level security;
alter table oshepherd_spike.admin_operations enable row level security;

create or replace function oshepherd_spike.timestamp_wire(value timestamptz)
returns text language sql immutable set search_path = pg_catalog as $$
  select pg_catalog.to_char(value at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
$$;
create or replace function oshepherd_spike.answer(value jsonb)
returns jsonb language sql immutable set search_path = pg_catalog as $$
  select pg_catalog.jsonb_build_object('v', 1, 'data', value)
$$;
create or replace function oshepherd_spike.check_request(value jsonb, fields text[])
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
create or replace function oshepherd_spike.uuid_field(value jsonb, field text)
returns uuid language plpgsql set search_path = pg_catalog as $$
begin
  if pg_catalog.jsonb_typeof(value->field) is distinct from 'string' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  return (value->>field)::uuid;
exception when invalid_text_representation then
  raise sqlstate 'PT422' using message = 'invalid_request';
end $$;
create or replace function oshepherd_spike.jwt_role()
returns text language sql stable set search_path = pg_catalog as $$
  select nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb->>'role'
$$;
create or replace function oshepherd_spike.jwt_subject()
returns uuid language sql stable set search_path = pg_catalog as $$
  select (nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::jsonb->>'sub')::uuid
$$;
create or replace function oshepherd_spike.require_service()
returns void language plpgsql set search_path = pg_catalog as $$
begin
  if oshepherd_spike.jwt_role() is distinct from 'service_role' then
    raise sqlstate 'PT403' using message = 'forbidden';
  end if;
end $$;
create or replace function oshepherd_spike.admin_replay(p_request jsonb, kind_name text)
returns jsonb language plpgsql set search_path = pg_catalog as $$
declare operation uuid; receipt oshepherd_spike.admin_operations; fingerprint bytea;
begin
  perform oshepherd_spike.require_service();
  operation := oshepherd_spike.uuid_field(p_request, 'operation_id');
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(operation::text, 0));
  fingerprint := extensions.digest(pg_catalog.convert_to((p_request - 'operation_id')::text, 'UTF8'), 'sha256');
  select * into receipt from oshepherd_spike.admin_operations where operation_id = operation;
  if found then
    if receipt.kind <> kind_name or receipt.request_hash <> fingerprint then
      raise sqlstate 'PT409' using message = 'idempotency_conflict';
    end if;
    return receipt.response;
  end if;
  return null;
end $$;
create or replace function oshepherd_spike.admin_finish(p_request jsonb, kind_name text, value jsonb)
returns jsonb language plpgsql set search_path = pg_catalog as $$
declare response jsonb;
begin
  response := oshepherd_spike.answer(value);
  insert into oshepherd_spike.admin_operations(operation_id, kind, request_hash, response)
  values (oshepherd_spike.uuid_field(p_request, 'operation_id'), kind_name,
    extensions.digest(pg_catalog.convert_to((p_request - 'operation_id')::text, 'UTF8'), 'sha256'), response);
  return response;
end $$;

create or replace function oshepherd_spike.current_worker(p_request jsonb, check_session boolean default true)
returns oshepherd_spike.workers language plpgsql set search_path = pg_catalog as $$
declare worker oshepherd_spike.workers;
begin
  if oshepherd_spike.jwt_role() is distinct from 'authenticated' or oshepherd_spike.jwt_subject() is null then
    raise sqlstate 'PT401' using message = 'unauthorized';
  end if;
  select * into worker from oshepherd_spike.workers where auth_subject = oshepherd_spike.jwt_subject();
  if not found then raise sqlstate 'PT403' using message = 'forbidden'; end if;
  if worker.revoked_at is not null or not exists (
    select 1 from oshepherd_spike.memberships where cluster_id = worker.cluster_id
      and auth_subject = worker.auth_subject and role = 'worker' and revoked_at is null
  ) then raise sqlstate 'PT403' using message = 'worker_revoked'; end if;
  if check_session and (
    worker.session_id is distinct from oshepherd_spike.uuid_field(p_request, 'session_id')
    or pg_catalog.jsonb_typeof(p_request->'generation') is distinct from 'number'
    or worker.generation::text is distinct from p_request->>'generation'
  ) then raise sqlstate 'PT409' using message = 'stale_session'; end if;
  return worker;
end $$;

create or replace function oshepherd_spike_api.bootstrap_cluster(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare replay jsonb; cluster uuid; owner_id uuid;
begin
  perform oshepherd_spike.check_request(p_request, array['cluster_id','owner_subject']);
  replay := oshepherd_spike.admin_replay(p_request, 'bootstrap_cluster');
  if replay is not null then return replay; end if;
  cluster := oshepherd_spike.uuid_field(p_request, 'cluster_id');
  owner_id := oshepherd_spike.uuid_field(p_request, 'owner_subject');
  insert into oshepherd_spike.clusters(id, owner_subject) values (cluster, owner_id);
  insert into oshepherd_spike.memberships(cluster_id, auth_subject, role) values (cluster, owner_id, 'operator');
  return oshepherd_spike.admin_finish(p_request, 'bootstrap_cluster', pg_catalog.jsonb_build_object('cluster_id', cluster));
exception when unique_violation then raise sqlstate 'PT409' using message = 'idempotency_conflict';
  when foreign_key_violation then raise sqlstate 'PT422' using message = 'invalid_request';
end $$;
create or replace function oshepherd_spike_api.create_api_key(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare replay jsonb; cluster uuid; key_id uuid; raw_key text;
begin
  perform oshepherd_spike.check_request(p_request, array['cluster_id','key_id','raw_key']);
  replay := oshepherd_spike.admin_replay(p_request, 'create_api_key');
  if replay is not null then return replay; end if;
  cluster := oshepherd_spike.uuid_field(p_request, 'cluster_id');
  key_id := oshepherd_spike.uuid_field(p_request, 'key_id');
  raw_key := p_request->>'raw_key';
  if pg_catalog.jsonb_typeof(p_request->'raw_key') is distinct from 'string' or raw_key !~ '^osk_[A-Za-z0-9_-]{43}$' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  perform 1 from oshepherd_spike.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  insert into oshepherd_spike.api_keys(id, cluster_id, key_hash)
    values (key_id, cluster, extensions.digest(pg_catalog.convert_to(raw_key, 'UTF8'), 'sha256'));
  return oshepherd_spike.admin_finish(p_request, 'create_api_key', pg_catalog.jsonb_build_object('key_id', key_id, 'cluster_id', cluster));
exception when unique_violation then raise sqlstate 'PT409' using message = 'idempotency_conflict';
end $$;
create or replace function oshepherd_spike_api.enroll_worker(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare replay jsonb; cluster uuid; subject uuid; worker_id uuid; capacity integer;
begin
  perform oshepherd_spike.check_request(p_request, array['cluster_id','auth_subject','capacity']);
  replay := oshepherd_spike.admin_replay(p_request, 'enroll_worker');
  if replay is not null then return replay; end if;
  cluster := oshepherd_spike.uuid_field(p_request, 'cluster_id');
  subject := oshepherd_spike.uuid_field(p_request, 'auth_subject');
  if pg_catalog.jsonb_typeof(p_request->'capacity') is distinct from 'number' or p_request->>'capacity' !~ '^[0-9]{1,2}$' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  capacity := (p_request->>'capacity')::integer;
  if capacity not between 1 and 64 then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  perform 1 from oshepherd_spike.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  insert into oshepherd_spike.memberships(cluster_id, auth_subject, role) values (cluster, subject, 'worker');
  insert into oshepherd_spike.workers(cluster_id, auth_subject, capacity) values (cluster, subject, capacity) returning id into worker_id;
  return oshepherd_spike.admin_finish(p_request, 'enroll_worker', pg_catalog.jsonb_build_object('worker_id', worker_id, 'cluster_id', cluster, 'capacity', capacity));
exception when unique_violation then raise sqlstate 'PT409' using message = 'idempotency_conflict';
  when foreign_key_violation then raise sqlstate 'PT422' using message = 'invalid_request';
end $$;
create or replace function oshepherd_spike_api.revoke_worker(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare replay jsonb; cluster uuid; worker_id uuid; subject uuid;
begin
  perform oshepherd_spike.check_request(p_request, array['cluster_id','worker_id']);
  replay := oshepherd_spike.admin_replay(p_request, 'revoke_worker');
  if replay is not null then return replay; end if;
  cluster := oshepherd_spike.uuid_field(p_request, 'cluster_id');
  worker_id := oshepherd_spike.uuid_field(p_request, 'worker_id');
  perform 1 from oshepherd_spike.clusters where id = cluster for update;
  update oshepherd_spike.workers set revoked_at = pg_catalog.clock_timestamp(), draining = true
    where id = worker_id and cluster_id = cluster returning auth_subject into subject;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  update oshepherd_spike.memberships set revoked_at = pg_catalog.clock_timestamp()
    where cluster_id = cluster and auth_subject = subject;
  return oshepherd_spike.admin_finish(p_request, 'revoke_worker', pg_catalog.jsonb_build_object('worker_id', worker_id, 'revoked', true));
end $$;
create or replace function oshepherd_spike_api.register_worker(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd_spike.workers; receipt oshepherd_spike.worker_sessions; operation uuid;
begin
  perform oshepherd_spike.check_request(p_request, array[]::text[]);
  operation := oshepherd_spike.uuid_field(p_request, 'operation_id');
  worker := oshepherd_spike.current_worker(p_request, false);
  perform 1 from oshepherd_spike.clusters where id = worker.cluster_id for update;
  perform 1 from oshepherd_spike.workers where id = worker.id for update;
  worker := oshepherd_spike.current_worker(p_request, false);
  select * into receipt from oshepherd_spike.worker_sessions where worker_id = worker.id and operation_id = operation;
  if found then
    if receipt.session_id <> worker.session_id or receipt.generation <> worker.generation then
      raise sqlstate 'PT409' using message = 'stale_session';
    end if;
  else
    update oshepherd_spike.workers set generation = generation + 1, session_id = pg_catalog.gen_random_uuid(),
      heartbeat_at = pg_catalog.clock_timestamp(), draining = false where id = worker.id returning * into worker;
    insert into oshepherd_spike.worker_sessions(worker_id, operation_id, generation, session_id)
      values (worker.id, operation, worker.generation, worker.session_id);
  end if;
  return oshepherd_spike.answer(pg_catalog.jsonb_build_object(
    'worker_id', worker.id, 'cluster_id', worker.cluster_id, 'capacity', worker.capacity,
    'session_id', worker.session_id, 'generation', worker.generation,
    'server_now', oshepherd_spike.timestamp_wire(pg_catalog.clock_timestamp()), 'worker_lease_ms', 45000
  ));
end $$;

-- The migration identity can also reset grants on its narrowly owned
-- maintenance functions when the complete spike is reapplied.
reset role;
revoke all on all tables in schema oshepherd_spike from public, anon, authenticated, service_role;
revoke all on all functions in schema oshepherd_spike from public, anon, authenticated, service_role;
revoke all on all functions in schema oshepherd_spike_api from public, anon, authenticated, service_role;
grant execute on function oshepherd_spike_api.bootstrap_cluster(jsonb), oshepherd_spike_api.create_api_key(jsonb),
  oshepherd_spike_api.enroll_worker(jsonb), oshepherd_spike_api.revoke_worker(jsonb) to service_role;
grant execute on function oshepherd_spike_api.register_worker(jsonb) to authenticated;
notify pgrst, 'reload schema';
commit;
