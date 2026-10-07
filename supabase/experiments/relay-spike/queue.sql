-- Logged queue feasibility spike. Apply only after identity.sql on the isolated
-- development stack. All mutations acquire cluster -> worker -> job locks.
begin;
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_spike_schema_v1', 0));
set local role oshepherd_spike_runtime;

create table if not exists oshepherd_spike.jobs (
  id uuid primary key default pg_catalog.gen_random_uuid(),
  cluster_id uuid not null references oshepherd_spike.clusters(id),
  key_id uuid not null references oshepherd_spike.api_keys(id),
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
  worker_id uuid references oshepherd_spike.workers(id),
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
create index if not exists jobs_ready on oshepherd_spike.jobs(cluster_id, state, available_at, created_at);
create index if not exists jobs_worker on oshepherd_spike.jobs(worker_id, state);
alter table oshepherd_spike.jobs add column if not exists updated_at timestamptz not null default pg_catalog.clock_timestamp();
create or replace function oshepherd_spike.touch_job()
returns trigger language plpgsql set search_path = pg_catalog as $$
begin new.updated_at := pg_catalog.clock_timestamp(); return new; end $$;
create or replace trigger touch_job before update on oshepherd_spike.jobs for each row execute function oshepherd_spike.touch_job();
create table if not exists oshepherd_spike.chunks (
  job_id uuid not null references oshepherd_spike.jobs(id) on delete cascade,
  seq integer not null,
  attempt_id uuid not null,
  content text not null,
  checksum text not null,
  size_bytes integer not null check (size_bytes between 1 and 65536),
  primary key (job_id, seq)
);
create table if not exists oshepherd_spike.worker_operations (
  worker_id uuid not null references oshepherd_spike.workers(id),
  operation_id uuid not null,
  job_id uuid references oshepherd_spike.jobs(id) on delete cascade,
  request_hash bytea not null,
  kind text not null,
  response jsonb not null,
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  primary key (worker_id, operation_id)
);
create index if not exists worker_operations_age on oshepherd_spike.worker_operations(created_at,worker_id,operation_id);
create index if not exists worker_operations_job on oshepherd_spike.worker_operations(job_id);
create index if not exists jobs_expiry on oshepherd_spike.jobs(cluster_id,content_expires_at,id);
-- Durable deletion intent survives a purge response being lost. No job FK:
-- tombstone removal must not discard pending object cleanup.
create table if not exists oshepherd_spike.result_deletions (
  cluster_id uuid not null references oshepherd_spike.clusters(id) on delete cascade,
  object_path text primary key,
  available_at timestamptz not null default pg_catalog.clock_timestamp()+interval '5 minutes',
  claim_id uuid,
  leased_until timestamptz,
  attempts integer not null default 0 check(attempts >= 0),
  created_at timestamptz not null default pg_catalog.clock_timestamp(),
  check(object_path ~ '^[a-f0-9-]{36}/[a-f0-9-]{36}/[a-f0-9-]{36}/result[.]json$'),
  check(pg_catalog.left(object_path,37) = cluster_id::text || '/')
);
create index if not exists result_deletions_ready on oshepherd_spike.result_deletions(cluster_id,available_at,leased_until);
alter table oshepherd_spike.jobs enable row level security;
alter table oshepherd_spike.chunks enable row level security;
alter table oshepherd_spike.worker_operations enable row level security;
alter table oshepherd_spike.result_deletions enable row level security;

create or replace function oshepherd_spike.check_fields(value jsonb, fields text[], nullable text[] default array[]::text[])
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
create or replace function oshepherd_spike.check_wire(value jsonb, fields text[], nullable text[] default array[]::text[])
returns void language plpgsql set search_path = pg_catalog as $$
begin
  -- Caller requests stay at 1 MiB. A final 1 MiB record can expand when encoded
  -- as JSON strings; only the completion RPC gets the larger control envelope.
  if pg_catalog.octet_length(value::text) > (case when fields @> array['final_chunks','result'] then 4194304 else 1048576 end) then
    raise sqlstate 'PT413' using message = 'request_too_large';
  end if;
  perform oshepherd_spike.check_fields(value, pg_catalog.array_cat(fields, array['v','operation_id']), nullable);
  if value->>'v' is distinct from '1' or pg_catalog.jsonb_typeof(value->'v') is distinct from 'number' then
    raise sqlstate 'PT409' using message = 'unsupported_protocol';
  end if;
  perform oshepherd_spike.uuid_field(value, 'operation_id');
end $$;
create or replace function oshepherd_spike.integer_field(value jsonb, field text, low integer, high integer)
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
create or replace function oshepherd_spike.lock_worker(p_request jsonb)
returns oshepherd_spike.workers language plpgsql set search_path = pg_catalog as $$
declare worker oshepherd_spike.workers;
begin
  worker := oshepherd_spike.current_worker(p_request);
  perform 1 from oshepherd_spike.clusters where id = worker.cluster_id for update;
  perform 1 from oshepherd_spike.workers where id = worker.id for update;
  return oshepherd_spike.current_worker(p_request);
end $$;
create or replace function oshepherd_spike.caller(p_request jsonb)
returns oshepherd_spike.api_keys language plpgsql set search_path = pg_catalog as $$
declare caller oshepherd_spike.api_keys; cluster uuid;
begin
  perform oshepherd_spike.require_service();
  cluster := oshepherd_spike.uuid_field(p_request, 'cluster_id');
  if pg_catalog.jsonb_typeof(p_request->'key') is distinct from 'string' or pg_catalog.length(p_request->>'key') > 256 then
    raise sqlstate 'PT401' using message = 'unauthorized';
  end if;
  perform 1 from oshepherd_spike.clusters where id = cluster and enabled for update;
  if not found then raise sqlstate 'PT401' using message = 'unauthorized'; end if;
  select * into caller from oshepherd_spike.api_keys where cluster_id = cluster and revoked_at is null
    and key_hash = extensions.digest(pg_catalog.convert_to(p_request->>'key', 'UTF8'), 'sha256');
  if not found then raise sqlstate 'PT401' using message = 'unauthorized'; end if;
  return caller;
end $$;
create or replace function oshepherd_spike.worker_replay(p_request jsonb, worker uuid, kind_name text)
returns jsonb language plpgsql set search_path = pg_catalog as $$
declare receipt oshepherd_spike.worker_operations;
begin
  select * into receipt from oshepherd_spike.worker_operations
    where worker_id = worker and operation_id = oshepherd_spike.uuid_field(p_request, 'operation_id');
  if found then
    if receipt.kind <> kind_name or receipt.request_hash <> extensions.digest(pg_catalog.convert_to(p_request::text, 'UTF8'), 'sha256') then
      raise sqlstate 'PT409' using message = 'idempotency_conflict';
    end if;
    return receipt.response;
  end if;
  return null;
end $$;
create or replace function oshepherd_spike.worker_finish(p_request jsonb, worker uuid, kind_name text, job uuid, value jsonb)
returns jsonb language plpgsql set search_path = pg_catalog as $$
declare response jsonb;
begin
  response := oshepherd_spike.answer(value);
  insert into oshepherd_spike.worker_operations(worker_id, operation_id, job_id, request_hash, kind, response)
    values(worker, oshepherd_spike.uuid_field(p_request, 'operation_id'), job,
      extensions.digest(pg_catalog.convert_to(p_request::text, 'UTF8'), 'sha256'), kind_name, response);
  return response;
end $$;
create or replace function oshepherd_spike.terminal(job uuid, terminal_state text, problem jsonb default null)
returns void language plpgsql set search_path = pg_catalog as $$
begin
  update oshepherd_spike.jobs set state = terminal_state, error = problem,
    finished_at = pg_catalog.clock_timestamp(), lease_until = null,
    content_expires_at = pg_catalog.clock_timestamp() + case when mode = 'async' then interval '1 day' else interval '30 minutes' end,
    tombstone_expires_at = pg_catalog.clock_timestamp() + interval '2 days'
    where id = job and state in ('queued','running');
end $$;
create or replace function oshepherd_spike.retry_or_fail(job oshepherd_spike.jobs, retryable boolean, problem jsonb)
returns void language plpgsql set search_path = pg_catalog as $$
declare delay_seconds integer;
begin
  delay_seconds := least(60, (2 ^ greatest(0, job.attempts - 1))::integer);
  if retryable and job.output_bytes = 0 and job.attempts < 6
    and job.execution_deadline > pg_catalog.clock_timestamp() + pg_catalog.make_interval(secs => delay_seconds)
    and job.queue_deadline > pg_catalog.clock_timestamp() + pg_catalog.make_interval(secs => delay_seconds) then
    update oshepherd_spike.jobs set state = 'queued', worker_id = null, session_id = null, generation = null,
      attempt_id = null, lease_until = null, available_at = pg_catalog.clock_timestamp() + pg_catalog.make_interval(secs => delay_seconds),
      error = problem where id = job.id;
  else
    perform oshepherd_spike.terminal(job.id, 'failed', problem);
  end if;
end $$;
-- Caller holds the cluster lock. Every invocation processes a finite prefix.
create or replace function oshepherd_spike.reap(cluster uuid, batch_limit integer)
returns integer language plpgsql set search_path = pg_catalog as $$
declare job oshepherd_spike.jobs; count integer := 0; now_at timestamptz := pg_catalog.clock_timestamp();
begin
  for job in select j.* from oshepherd_spike.jobs j left join oshepherd_spike.workers w on w.id = j.worker_id
    where j.cluster_id = cluster and j.state in ('queued','running') and (
      j.execution_deadline <= now_at or (j.state = 'queued' and j.queue_deadline <= now_at)
      or (j.mode = 'sync' and j.consumer_until <= now_at)
      or (j.state = 'running' and (j.lease_until <= now_at or w.revoked_at is not null
        or w.generation is distinct from j.generation or w.session_id is distinct from j.session_id)))
    order by j.id limit batch_limit for update of j loop
    count := count + 1;
    if job.execution_deadline <= now_at or (job.state = 'queued' and job.queue_deadline <= now_at) then
      perform oshepherd_spike.terminal(job.id, 'expired', '{"code":"execution_expired","message":"Job deadline elapsed"}');
    elsif job.mode = 'sync' and job.consumer_until <= now_at then
      perform oshepherd_spike.terminal(job.id, 'cancelled', '{"code":"consumer_expired","message":"Consumer lease elapsed"}');
    else
      perform oshepherd_spike.retry_or_fail(job, true, '{"code":"worker_lost","message":"Worker lease elapsed"}');
    end if;
  end loop;
  return count;
end $$;
create or replace function oshepherd_spike.job_wire(job oshepherd_spike.jobs)
returns jsonb language sql stable set search_path = pg_catalog as $$
  select pg_catalog.jsonb_build_object('job_id', job.id, 'state', job.state, 'mode', job.mode,
    'created_at', oshepherd_spike.timestamp_wire(job.created_at),
    'updated_at', oshepherd_spike.timestamp_wire(job.updated_at),
    'execution_deadline', oshepherd_spike.timestamp_wire(job.execution_deadline),
    'finished_at', oshepherd_spike.timestamp_wire(job.finished_at),
    'content_expires_at', oshepherd_spike.timestamp_wire(job.content_expires_at),
    'content_available', job.content_expires_at is null or job.content_expires_at > pg_catalog.clock_timestamp(),
    'attempts', job.attempts, 'last_seq', job.last_seq, 'output_bytes', job.output_bytes, 'error', job.error)
$$;
create or replace function oshepherd_spike.attempt(p_request jsonb, worker oshepherd_spike.workers)
returns oshepherd_spike.jobs language plpgsql set search_path = pg_catalog as $$
declare job oshepherd_spike.jobs;
begin
  select * into job from oshepherd_spike.jobs where id = oshepherd_spike.uuid_field(p_request, 'job_id')
    and cluster_id = worker.cluster_id for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  if job.state <> 'running' or job.worker_id <> worker.id or job.session_id <> worker.session_id
    or job.generation <> worker.generation or job.attempt_id <> oshepherd_spike.uuid_field(p_request, 'attempt_id')
    or job.lease_until <= pg_catalog.clock_timestamp() or job.execution_deadline <= pg_catalog.clock_timestamp()
    or (job.mode = 'sync' and job.consumer_until <= pg_catalog.clock_timestamp()) then
    raise sqlstate 'PT409' using message = 'stale_attempt';
  end if;
  return job;
end $$;

create or replace function oshepherd_spike_api.update_models(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd_spike.workers; model jsonb; replay jsonb;
begin
  perform oshepherd_spike.check_wire(p_request, array['session_id','generation','models','running','version']);
  worker := oshepherd_spike.lock_worker(p_request);
  replay := oshepherd_spike.worker_replay(p_request, worker.id, 'update_models');
  if replay is not null then return replay; end if;
  if pg_catalog.jsonb_typeof(p_request->'models') is distinct from 'array' or pg_catalog.jsonb_array_length(p_request->'models') > 256
    or pg_catalog.jsonb_typeof(p_request->'running') is distinct from 'object' or pg_catalog.jsonb_typeof(p_request->'version') is distinct from 'object' then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  delete from oshepherd_spike.worker_models where worker_id = worker.id;
  for model in select value from pg_catalog.jsonb_array_elements(p_request->'models') loop
    perform oshepherd_spike.check_fields(model, array['name','digest','operations','details','show']);
    if pg_catalog.jsonb_typeof(model->'name') is distinct from 'string' or pg_catalog.length(model->>'name') not between 1 and 256
      or pg_catalog.jsonb_typeof(model->'digest') is distinct from 'string' or model->>'digest' !~ '^[a-f0-9]{64}$'
      or pg_catalog.jsonb_typeof(model->'operations') is distinct from 'array' or pg_catalog.jsonb_array_length(model->'operations') > 4
      or exists(select 1 from pg_catalog.jsonb_array_elements_text(model->'operations') op where op not in ('generate','chat','embeddings','systemone'))
      or pg_catalog.jsonb_typeof(model->'details') is distinct from 'object' or pg_catalog.jsonb_typeof(model->'show') is distinct from 'object' then
      raise sqlstate 'PT422' using message = 'invalid_request';
    end if;
    insert into oshepherd_spike.worker_models(worker_id, name, digest, operations, details, show)
      values(worker.id, model->>'name', model->>'digest', model->'operations', model->'details', model->'show');
  end loop;
  update oshepherd_spike.workers set running = p_request->'running', version = p_request->'version',
    heartbeat_at = pg_catalog.clock_timestamp() where id = worker.id;
  return oshepherd_spike.worker_finish(p_request, worker.id, 'update_models', null, '{"updated":true}');
exception when unique_violation then raise sqlstate 'PT422' using message = 'invalid_request';
end $$;

create or replace function oshepherd_spike_api.submit_job(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare caller oshepherd_spike.api_keys; cluster oshepherd_spike.clusters; job oshepherd_spike.jobs;
  limits jsonb; fingerprint bytea; model_name text; digest text; digest_count integer; token uuid;
  queue_ms integer; total_ms integer; max_bytes integer; now_at timestamptz := pg_catalog.clock_timestamp();
begin
  perform oshepherd_spike.check_wire(p_request, array['cluster_id','key','request','mode','limits','idempotency_key','consumer_token'], array['idempotency_key','consumer_token']);
  caller := oshepherd_spike.caller(p_request);
  select * into cluster from oshepherd_spike.clusters where id = caller.cluster_id;
  perform oshepherd_spike.check_fields(p_request->'request', array['type','payload']);
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
  queue_ms := oshepherd_spike.integer_field(limits, 'queue_ms', 1, 3600000);
  total_ms := oshepherd_spike.integer_field(limits, 'total_ms', 1, case when p_request->>'mode' = 'async' then 86400000 else 135000 end);
  max_bytes := oshepherd_spike.integer_field(limits, 'max_output_bytes', 1, 33554432);
  fingerprint := extensions.digest(pg_catalog.convert_to(pg_catalog.jsonb_build_object('request',p_request->'request','mode',p_request->'mode','limits',limits)::text,'UTF8'),'sha256');
  if p_request->'idempotency_key' <> 'null'::jsonb and (pg_catalog.jsonb_typeof(p_request->'idempotency_key') <> 'string'
    or pg_catalog.length(p_request->>'idempotency_key') not between 1 and 128) then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  if p_request->'consumer_token' <> 'null'::jsonb then token := oshepherd_spike.uuid_field(p_request,'consumer_token'); end if;
  if (p_request->>'mode' = 'sync') <> (token is not null) then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  select * into job from oshepherd_spike.jobs where key_id = caller.id and
    (submit_operation = oshepherd_spike.uuid_field(p_request,'operation_id') or idempotency_key = p_request->>'idempotency_key');
  if found then
    if job.request_hash <> fingerprint then raise sqlstate 'PT409' using message = 'idempotency_conflict'; end if;
    -- Consumer identity is never disclosed or replaced by an idempotency replay.
    if job.mode = 'sync' and job.consumer_token is distinct from token then raise sqlstate 'PT409' using message = 'consumer_conflict'; end if;
    return oshepherd_spike.answer(oshepherd_spike.job_wire(job));
  end if;
  perform oshepherd_spike.reap(cluster.id, 128);
  if (select count(*) from oshepherd_spike.jobs where cluster_id = cluster.id and state in ('queued','running')) >= cluster.max_active
    or (select count(*) from oshepherd_spike.jobs where cluster_id = cluster.id and state = 'queued') >= cluster.max_queued
    or (select count(*) from oshepherd_spike.jobs where key_id = caller.id and state in ('queued','running')) >= cluster.caller_max_active then
    raise sqlstate 'PT429' using message = 'capacity_exceeded';
  end if;
  model_name := p_request #>> '{request,payload,model}';
  if pg_catalog.strpos(model_name, ':') = 0 then model_name := model_name || ':latest'; end if;
  select count(distinct m.digest), min(m.digest) into digest_count, digest from oshepherd_spike.worker_models m
    join oshepherd_spike.workers w on w.id = m.worker_id where w.cluster_id = cluster.id and w.revoked_at is null
      and not w.draining and w.heartbeat_at > now_at - interval '60 seconds' and m.observed_at > now_at - interval '60 seconds'
      and m.name = model_name and m.operations ? (p_request #>> '{request,type}');
  if digest_count = 0 then raise sqlstate 'PT503' using message = 'model_unavailable'; end if;
  if digest_count > 1 then raise sqlstate 'PT409' using message = 'model_digest_conflict'; end if;
  insert into oshepherd_spike.jobs(cluster_id, key_id, submit_operation, idempotency_key, request_hash, request,
    mode, model_name, model_digest, stream, queue_deadline, execution_deadline, consumer_token, consumer_until, max_output_bytes)
    values(cluster.id, caller.id, oshepherd_spike.uuid_field(p_request,'operation_id'), p_request->>'idempotency_key', fingerprint,
      p_request->'request', p_request->>'mode', model_name, digest, (p_request #>> '{request,type}' in ('generate','chat')) and coalesce(p_request #> '{request,payload,stream}' = 'true'::jsonb,false),
      now_at + pg_catalog.make_interval(secs => least(queue_ms,total_ms)/1000.0), now_at + pg_catalog.make_interval(secs => total_ms/1000.0),
      token, case when token is not null then now_at + interval '60 seconds' end, max_bytes) returning * into job;
  return oshepherd_spike.answer(oshepherd_spike.job_wire(job));
end $$;

create or replace function oshepherd_spike_api.heartbeat_and_claim(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd_spike.workers; job oshepherd_spike.jobs; active jsonb; replay jsonb;
  claims jsonb := '[]'; stopped jsonb := '[]'; maximum integer; slots integer; now_at timestamptz := pg_catalog.clock_timestamp();
begin
  perform oshepherd_spike.check_wire(p_request,array['session_id','generation','active_attempts','max_jobs','draining']);
  worker := oshepherd_spike.lock_worker(p_request);
  maximum := oshepherd_spike.integer_field(p_request,'max_jobs',0,64);
  if pg_catalog.jsonb_typeof(p_request->'draining') is distinct from 'boolean'
    or pg_catalog.jsonb_typeof(p_request->'active_attempts') is distinct from 'array'
    or pg_catalog.jsonb_array_length(p_request->'active_attempts') > 64 then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  perform oshepherd_spike.reap(worker.cluster_id,128);
  update oshepherd_spike.workers set heartbeat_at = now_at, draining = (p_request->>'draining')::boolean where id = worker.id;
  for active in select value from pg_catalog.jsonb_array_elements(p_request->'active_attempts') loop
    perform oshepherd_spike.check_fields(active,array['job_id','attempt_id']);
    update oshepherd_spike.jobs set lease_until = least(execution_deadline, now_at + interval '45 seconds')
      where id = oshepherd_spike.uuid_field(active,'job_id') and attempt_id = oshepherd_spike.uuid_field(active,'attempt_id')
      and worker_id = worker.id and generation = worker.generation and session_id = worker.session_id and state = 'running';
    if not found then stopped := stopped || pg_catalog.jsonb_build_array(active); end if;
  end loop;
  -- Replaying a lost claim response returns the assigned attempt without a
  -- second admission. Leases renew only for the caller's reported active set.
  replay := oshepherd_spike.worker_replay(p_request,worker.id,'heartbeat_and_claim');
  if replay is not null then
    for active in select value from pg_catalog.jsonb_array_elements(replay #> '{data,jobs}') loop
      if not exists(select 1 from oshepherd_spike.jobs where id = (active->>'job_id')::uuid and attempt_id = (active->>'attempt_id')::uuid
        and state = 'running' and session_id = worker.session_id) then raise sqlstate 'PT409' using message = 'stale_attempt'; end if;
    end loop;
    return replay;
  end if;
  slots := least(maximum, worker.capacity - (select count(*) from oshepherd_spike.jobs where worker_id = worker.id and state = 'running'),
    (select max_running from oshepherd_spike.clusters where id = worker.cluster_id) -
      (select count(*) from oshepherd_spike.jobs where cluster_id = worker.cluster_id and state = 'running'));
  if not (p_request->>'draining')::boolean and slots > 0 then
    for job in select j.* from oshepherd_spike.jobs j where j.cluster_id = worker.cluster_id and j.state = 'queued' and j.available_at <= now_at
      and j.queue_deadline > now_at and j.execution_deadline > now_at and exists(select 1 from oshepherd_spike.worker_models m
        where m.worker_id = worker.id and m.name = j.model_name and m.digest = j.model_digest
        and m.observed_at > now_at - interval '60 seconds' and m.operations ? (j.request->>'type'))
      order by j.created_at,j.id limit slots for update loop
      update oshepherd_spike.jobs set state = 'running', worker_id = worker.id, generation = worker.generation, session_id = worker.session_id,
        attempt_id = pg_catalog.gen_random_uuid(), attempts = attempts + 1, error = null,
        lease_until = least(execution_deadline,now_at + interval '45 seconds') where id = job.id returning * into job;
      claims := claims || pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('job_id',job.id,'attempt_id',job.attempt_id,
        'request',job.request,'mode',job.mode,'model_digest',job.model_digest,'execution_deadline',oshepherd_spike.timestamp_wire(job.execution_deadline),
        'max_output_bytes',job.max_output_bytes));
    end loop;
  end if;
  return oshepherd_spike.worker_finish(p_request,worker.id,'heartbeat_and_claim',null,
    pg_catalog.jsonb_build_object('jobs',claims,'stop',stopped,'server_now',oshepherd_spike.timestamp_wire(now_at)));
end $$;

create or replace function oshepherd_spike.append(job oshepherd_spike.jobs, sequence integer, content text, checksum text)
returns void language plpgsql set search_path = pg_catalog as $$
declare chunk oshepherd_spike.chunks; size integer := pg_catalog.octet_length(content);
begin
  if size not between 1 and 65536 or checksum is distinct from pg_catalog.encode(extensions.digest(pg_catalog.convert_to(content,'UTF8'),'sha256'),'hex') then
    raise sqlstate 'PT422' using message = 'invalid_chunk';
  end if;
  select * into chunk from oshepherd_spike.chunks where job_id = job.id and seq = sequence;
  if found then
    if chunk.attempt_id <> job.attempt_id or chunk.checksum <> checksum or chunk.content <> content then
      raise sqlstate 'PT409' using message = 'idempotency_conflict';
    end if;
    return;
  end if;
  if sequence <> job.last_seq + 1 then raise sqlstate 'PT409' using message = 'sequence_gap'; end if;
  if job.output_bytes + size > job.max_output_bytes then raise sqlstate 'PT413' using message = 'output_limit_exceeded'; end if;
  insert into oshepherd_spike.chunks(job_id,seq,attempt_id,content,checksum,size_bytes) values(job.id,sequence,job.attempt_id,content,checksum,size);
  update oshepherd_spike.jobs set last_seq = sequence, output_bytes = output_bytes + size where id = job.id;
end $$;
create or replace function oshepherd_spike_api.append_output(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd_spike.workers; job oshepherd_spike.jobs; replay jsonb; sequence integer;
begin
  perform oshepherd_spike.check_wire(p_request,array['session_id','generation','job_id','attempt_id','seq','content','checksum']);
  worker := oshepherd_spike.lock_worker(p_request);
  replay := oshepherd_spike.worker_replay(p_request,worker.id,'append_output');
  if replay is not null then return replay; end if;
  job := oshepherd_spike.attempt(p_request,worker);
  if not job.stream then raise sqlstate 'PT409' using message = 'invalid_output_mode'; end if;
  if pg_catalog.jsonb_typeof(p_request->'content') is distinct from 'string' or pg_catalog.jsonb_typeof(p_request->'checksum') is distinct from 'string' then
    raise sqlstate 'PT422' using message = 'invalid_chunk';
  end if;
  sequence := oshepherd_spike.integer_field(p_request,'seq',1,2147483647);
  perform oshepherd_spike.append(job,sequence,p_request->>'content',p_request->>'checksum');
  return oshepherd_spike.worker_finish(p_request,worker.id,'append_output',job.id,pg_catalog.jsonb_build_object('seq',sequence));
end $$;
create or replace function oshepherd_spike_api.finish_job(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd_spike.workers; job oshepherd_spike.jobs; replay jsonb; chunk jsonb; result_descriptor jsonb; content text; terminal_record text; size integer;
begin
  perform oshepherd_spike.check_wire(p_request,array['session_id','generation','job_id','attempt_id','final_chunks','result'],array['result']);
  worker := oshepherd_spike.lock_worker(p_request);
  replay := oshepherd_spike.worker_replay(p_request,worker.id,'finish_job');
  if replay is not null then return replay; end if;
  job := oshepherd_spike.attempt(p_request,worker);
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
      perform oshepherd_spike.append(job,job.last_seq+1,content,pg_catalog.encode(extensions.digest(pg_catalog.convert_to(content,'UTF8'),'sha256'),'hex'));
      select * into job from oshepherd_spike.jobs where id = job.id;
    end loop;
  else
    if pg_catalog.jsonb_array_length(p_request->'final_chunks') <> 0 then raise sqlstate 'PT422' using message = 'invalid_output_mode'; end if;
    result_descriptor := p_request->'result';
    perform oshepherd_spike.check_fields(result_descriptor,array['inline','object_path','size_bytes','checksum','content_type'],array['inline','object_path']);
    if result_descriptor->>'content_type' <> 'application/json' or pg_catalog.jsonb_typeof(result_descriptor->'checksum') is distinct from 'string'
      or result_descriptor->>'checksum' !~ '^[a-f0-9]{64}$' then
      raise sqlstate 'PT422' using message = 'invalid_result';
    end if;
    size := oshepherd_spike.integer_field(result_descriptor,'size_bytes',1,33554432);
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
      insert into oshepherd_spike.result_objects(job_id,bucket_id,object_path)
        values(job.id,'oshepherd-spike-results',result_descriptor->>'object_path');
    end if;
    update oshepherd_spike.jobs set result = result_descriptor, output_bytes = size where id = job.id;
  end if;
  perform oshepherd_spike.terminal(job.id,'succeeded');
  return oshepherd_spike.worker_finish(p_request,worker.id,'finish_job',job.id,'{"state":"succeeded"}');
exception when invalid_text_representation then raise sqlstate 'PT422' using message = 'invalid_result';
  when foreign_key_violation then raise sqlstate 'PT409' using message = 'result_object_missing';
end $$;
create or replace function oshepherd_spike_api.fail_attempt(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare worker oshepherd_spike.workers; job oshepherd_spike.jobs; replay jsonb;
begin
  perform oshepherd_spike.check_wire(p_request,array['session_id','generation','job_id','attempt_id','error','retryable']);
  worker := oshepherd_spike.lock_worker(p_request);
  replay := oshepherd_spike.worker_replay(p_request,worker.id,'fail_attempt');
  if replay is not null then return replay; end if;
  job := oshepherd_spike.attempt(p_request,worker);
  perform oshepherd_spike.check_fields(p_request->'error',array['code','message']);
  if pg_catalog.jsonb_typeof(p_request->'retryable') is distinct from 'boolean'
    or pg_catalog.jsonb_typeof(p_request #> '{error,code}') is distinct from 'string'
    or p_request #>> '{error,code}' !~ '^[a-z][a-z0-9_]{0,63}$'
    or pg_catalog.jsonb_typeof(p_request #> '{error,message}') is distinct from 'string'
    or pg_catalog.length(p_request #>> '{error,message}') > 512 then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  perform oshepherd_spike.retry_or_fail(job,(p_request->>'retryable')::boolean,p_request->'error');
  select * into job from oshepherd_spike.jobs where id = job.id;
  return oshepherd_spike.worker_finish(p_request,worker.id,'fail_attempt',job.id,oshepherd_spike.job_wire(job));
end $$;

create or replace function oshepherd_spike_api.read_updates(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare caller oshepherd_spike.api_keys; cursor jsonb; job oshepherd_spike.jobs; chunk oshepherd_spike.chunks;
  jobs jsonb := '[]'; page jsonb; chunks jsonb; bytes_left integer; chunks_left integer; sequence integer; token uuid;
begin
  perform oshepherd_spike.check_wire(p_request,array['cluster_id','key','cursors','max_bytes','max_chunks']);
  caller := oshepherd_spike.caller(p_request);
  bytes_left := oshepherd_spike.integer_field(p_request,'max_bytes',1,262144);
  chunks_left := oshepherd_spike.integer_field(p_request,'max_chunks',1,64);
  if pg_catalog.jsonb_typeof(p_request->'cursors') is distinct from 'array' or pg_catalog.jsonb_array_length(p_request->'cursors') > 32 then
    raise sqlstate 'PT422' using message = 'invalid_request';
  end if;
  perform oshepherd_spike.reap(caller.cluster_id,128);
  for cursor in select value from pg_catalog.jsonb_array_elements(p_request->'cursors') loop
    perform oshepherd_spike.check_fields(cursor,array['job_id','cursor','consumer_token'],array['consumer_token']);
    token := null;
    if cursor->'consumer_token' <> 'null'::jsonb then token := oshepherd_spike.uuid_field(cursor,'consumer_token'); end if;
    sequence := oshepherd_spike.integer_field(cursor,'cursor',0,2147483647);
    select * into job from oshepherd_spike.jobs where id = oshepherd_spike.uuid_field(cursor,'job_id') and key_id = caller.id for update;
    if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
    if job.mode = 'sync' and job.consumer_token is distinct from token then raise sqlstate 'PT403' using message = 'forbidden'; end if;
    if sequence > job.last_seq then raise sqlstate 'PT409' using message = 'invalid_cursor'; end if;
    if job.state in ('queued','running') and job.mode = 'sync' then
      update oshepherd_spike.jobs set consumer_until = pg_catalog.clock_timestamp()+interval '60 seconds' where id = job.id;
    end if;
    chunks := '[]';
    if job.content_expires_at is null or job.content_expires_at > pg_catalog.clock_timestamp() then
      for chunk in select * from oshepherd_spike.chunks where job_id = job.id and seq > sequence order by seq limit chunks_left loop
        if chunk.size_bytes > bytes_left then exit; end if;
        bytes_left := bytes_left - chunk.size_bytes; chunks_left := chunks_left - 1; sequence := chunk.seq;
        chunks := chunks || pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object('seq',chunk.seq,'content',chunk.content,'checksum',chunk.checksum));
      end loop;
    end if;
    page := oshepherd_spike.job_wire(job) || pg_catalog.jsonb_build_object('chunks',chunks,'cursor',sequence,
      'result',case when job.content_expires_at > pg_catalog.clock_timestamp() and (job.result->>'object_path' is not null or job.output_bytes <= bytes_left) then job.result else null end,
      'result_omitted',job.result is not null and job.result->>'object_path' is null and job.content_expires_at > pg_catalog.clock_timestamp() and job.output_bytes > bytes_left);
    if job.result is not null and job.result->>'object_path' is null and job.content_expires_at > pg_catalog.clock_timestamp() and job.output_bytes <= bytes_left then
      bytes_left := bytes_left-job.output_bytes;
    end if;
    jobs := jobs || pg_catalog.jsonb_build_array(page);
  end loop;
  return oshepherd_spike.answer(pg_catalog.jsonb_build_object('jobs',jobs,'server_now',oshepherd_spike.timestamp_wire(pg_catalog.clock_timestamp())));
end $$;
create or replace function oshepherd_spike_api.cancel_job(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare caller oshepherd_spike.api_keys; job oshepherd_spike.jobs; token uuid;
begin
  perform oshepherd_spike.check_wire(p_request,array['cluster_id','key','job_id','consumer_token','reason'],array['consumer_token']);
  caller := oshepherd_spike.caller(p_request);
  if p_request->>'reason' not in ('cancelled','execution_expired','consumer_expired','reader_stalled') then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  select * into job from oshepherd_spike.jobs where id = oshepherd_spike.uuid_field(p_request,'job_id') and key_id = caller.id for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  if p_request->'consumer_token' <> 'null'::jsonb then token := oshepherd_spike.uuid_field(p_request,'consumer_token'); end if;
  if job.mode = 'sync' and job.consumer_token is distinct from token then raise sqlstate 'PT403' using message = 'forbidden'; end if;
  perform oshepherd_spike.terminal(job.id,case when p_request->>'reason' = 'execution_expired' then 'expired' else 'cancelled' end,
    pg_catalog.jsonb_build_object('code',p_request->>'reason','message','Job cancelled'));
  select * into job from oshepherd_spike.jobs where id = job.id;
  return oshepherd_spike.answer(oshepherd_spike.job_wire(job));
end $$;
create or replace function oshepherd_spike_api.backend_info(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare caller oshepherd_spike.api_keys;
begin
  perform oshepherd_spike.check_wire(p_request,array['cluster_id','key']); caller := oshepherd_spike.caller(p_request);
  return oshepherd_spike.answer(pg_catalog.jsonb_build_object('protocol',1,'schema','oshepherd_spike_api','experimental',true,
    'cluster_id',caller.cluster_id,'server_now',oshepherd_spike.timestamp_wire(pg_catalog.clock_timestamp())));
end $$;
create or replace function oshepherd_spike_api.read_metadata(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare caller oshepherd_spike.api_keys; response jsonb; model_name text; digest_count integer;
begin
  perform oshepherd_spike.check_wire(p_request,array['cluster_id','key','operation','model'],array['model']); caller := oshepherd_spike.caller(p_request);
  if p_request->>'operation' = 'tags' then
    select pg_catalog.jsonb_build_object('models',coalesce(pg_catalog.jsonb_agg(details order by name),'[]')) into response from (
      select distinct on (m.name) m.name,m.details from oshepherd_spike.worker_models m join oshepherd_spike.workers w on w.id = m.worker_id
        where w.cluster_id = caller.cluster_id and w.revoked_at is null and not w.draining and w.heartbeat_at > pg_catalog.clock_timestamp()-interval '60 seconds'
        and m.observed_at > pg_catalog.clock_timestamp()-interval '60 seconds' order by m.name,w.id) models;
  elsif p_request->>'operation' = 'ps' then
    select pg_catalog.jsonb_build_object('models',coalesce(pg_catalog.jsonb_agg(model),'[]')) into response from (
      select distinct model from oshepherd_spike.workers w cross join lateral pg_catalog.jsonb_array_elements(coalesce(w.running->'models','[]')) model
        where w.cluster_id = caller.cluster_id and w.revoked_at is null and w.heartbeat_at > pg_catalog.clock_timestamp()-interval '60 seconds') models;
  elsif p_request->>'operation' = 'version' then
    select w.version into response from oshepherd_spike.workers w where w.cluster_id = caller.cluster_id and w.revoked_at is null
      and w.heartbeat_at > pg_catalog.clock_timestamp()-interval '60 seconds' order by w.id limit 1;
    if response is null then response := '{}'; end if;
  elsif p_request->>'operation' = 'show' then
    if pg_catalog.jsonb_typeof(p_request->'model') is distinct from 'string' then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
    model_name := p_request->>'model'; if pg_catalog.strpos(model_name,':') = 0 then model_name := model_name || ':latest'; end if;
    select count(distinct m.digest) into digest_count from oshepherd_spike.worker_models m join oshepherd_spike.workers w on w.id = m.worker_id
      where w.cluster_id = caller.cluster_id and w.revoked_at is null and w.heartbeat_at > pg_catalog.clock_timestamp()-interval '60 seconds'
        and m.observed_at > pg_catalog.clock_timestamp()-interval '60 seconds' and m.name = model_name;
    if digest_count = 0 then raise sqlstate 'PT404' using message = 'model_not_found'; end if;
    if digest_count > 1 then raise sqlstate 'PT409' using message = 'model_digest_conflict'; end if;
    select m.show into response from oshepherd_spike.worker_models m join oshepherd_spike.workers w on w.id = m.worker_id
      where w.cluster_id = caller.cluster_id and w.revoked_at is null and w.heartbeat_at > pg_catalog.clock_timestamp()-interval '60 seconds'
        and m.observed_at > pg_catalog.clock_timestamp()-interval '60 seconds' and m.name = model_name order by w.id limit 1;
  else raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  return oshepherd_spike.answer(pg_catalog.jsonb_build_object('response',response));
end $$;
create or replace function oshepherd_spike_api.reap_jobs(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare cluster uuid;
begin
  perform oshepherd_spike.check_wire(p_request,array['cluster_id','limit']); perform oshepherd_spike.require_service();
  cluster := oshepherd_spike.uuid_field(p_request,'cluster_id');
  perform 1 from oshepherd_spike.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  return oshepherd_spike.answer(pg_catalog.jsonb_build_object('reaped',oshepherd_spike.reap(cluster,oshepherd_spike.integer_field(p_request,'limit',1,256))));
end $$;
create or replace function oshepherd_spike.purge(cluster uuid,batch_limit integer)
returns jsonb language plpgsql set search_path = pg_catalog as $$
declare job oshepherd_spike.jobs; purged integer := 0; paths jsonb := '[]';
begin
  if batch_limit not between 1 and 256 then raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  -- Caller holds the cluster lock. Cap chunk and receipt rows as well as jobs;
  -- a large expired stream cannot make one purge statement unbounded.
  for job in select * from oshepherd_spike.jobs where cluster_id = cluster and content_expires_at <= pg_catalog.clock_timestamp()
    and (request <> '{}'::jsonb or result is not null or tombstone_expires_at <= pg_catalog.clock_timestamp()) order by id
    limit batch_limit for update loop
    delete from oshepherd_spike.chunks where job_id = job.id and seq in(
      select seq from oshepherd_spike.chunks where job_id = job.id order by seq limit 64);
    if exists(select 1 from oshepherd_spike.chunks where job_id = job.id) then continue; end if;
    if job.tombstone_expires_at <= pg_catalog.clock_timestamp() then
      delete from oshepherd_spike.worker_operations where (worker_id,operation_id) in(
        select worker_id,operation_id from oshepherd_spike.worker_operations where job_id = job.id order by worker_id,operation_id limit 64);
      if exists(select 1 from oshepherd_spike.worker_operations where job_id = job.id) then continue; end if;
    end if;
    if job.result->>'object_path' is not null then
      insert into oshepherd_spike.result_deletions(cluster_id,object_path) values(cluster,job.result->>'object_path') on conflict(object_path) do nothing;
      paths := paths || pg_catalog.jsonb_build_array(job.result->>'object_path');
    end if;
    delete from oshepherd_spike.result_objects where job_id = job.id;
    update oshepherd_spike.jobs set request = '{}', result = null where id = job.id;
    if job.tombstone_expires_at <= pg_catalog.clock_timestamp() then delete from oshepherd_spike.jobs where id = job.id; end if;
    purged := purged + 1;
  end loop;
  delete from oshepherd_spike.worker_operations where (worker_id,operation_id) in(
    select o.worker_id,o.operation_id from oshepherd_spike.worker_operations o join oshepherd_spike.workers w on w.id = o.worker_id
      where w.cluster_id = cluster and o.created_at < pg_catalog.clock_timestamp()-interval '2 days'
      order by o.created_at,o.worker_id,o.operation_id limit batch_limit);
  return pg_catalog.jsonb_build_object('purged',purged,'object_paths',paths);
end $$;
create or replace function oshepherd_spike_api.purge_expired(p_request jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog as $$
declare cluster uuid;
begin
  perform oshepherd_spike.check_wire(p_request,array['cluster_id','limit']); perform oshepherd_spike.require_service();
  cluster := oshepherd_spike.uuid_field(p_request,'cluster_id');
  perform 1 from oshepherd_spike.clusters where id = cluster for update;
  if not found then raise sqlstate 'PT404' using message = 'not_found'; end if;
  return oshepherd_spike.answer(oshepherd_spike.purge(cluster,oshepherd_spike.integer_field(p_request,'limit',1,256)));
end $$;

reset role;
revoke all on all tables in schema oshepherd_spike from public, anon, authenticated, service_role;
revoke all on all functions in schema oshepherd_spike from public, anon, authenticated, service_role;
revoke all on all functions in schema oshepherd_spike_api from public, anon, authenticated, service_role;
grant execute on function oshepherd_spike_api.bootstrap_cluster(jsonb),oshepherd_spike_api.create_api_key(jsonb),
  oshepherd_spike_api.enroll_worker(jsonb),oshepherd_spike_api.revoke_worker(jsonb),oshepherd_spike_api.submit_job(jsonb),
  oshepherd_spike_api.read_updates(jsonb),oshepherd_spike_api.cancel_job(jsonb),oshepherd_spike_api.read_metadata(jsonb),
  oshepherd_spike_api.backend_info(jsonb),oshepherd_spike_api.reap_jobs(jsonb),oshepherd_spike_api.purge_expired(jsonb) to service_role;
grant execute on function oshepherd_spike_api.register_worker(jsonb),oshepherd_spike_api.update_models(jsonb),
  oshepherd_spike_api.heartbeat_and_claim(jsonb),oshepherd_spike_api.append_output(jsonb),oshepherd_spike_api.finish_job(jsonb),
  oshepherd_spike_api.fail_attempt(jsonb) to authenticated;
notify pgrst, 'reload schema';
commit;
