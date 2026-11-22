-- Private, attempt-scoped result objects for the isolated development profile.
begin;
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_spike_schema_v1',0));
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
  values('oshepherd-spike-results','oshepherd-spike-results',false,33554432,array['application/json'])
  on conflict(id) do nothing;
-- PostgreSQL's migration identity can create the Storage foreign key. The
-- runtime owner receives no Storage table SELECT grant: the FK enforces object
-- existence and immutable bucket/path association at commit.
create table if not exists oshepherd_spike.result_objects (
  job_id uuid primary key references oshepherd_spike.jobs(id) on delete cascade,
  bucket_id text not null check (bucket_id = 'oshepherd-spike-results'),
  object_path text not null,
  foreign key (bucket_id,object_path) references storage.objects(bucket_id,name)
);
alter table oshepherd_spike.result_objects owner to oshepherd_spike_runtime;
set local role oshepherd_spike_runtime;
alter table oshepherd_spike.result_objects enable row level security;
revoke all on oshepherd_spike.result_objects from public,anon,authenticated,service_role;
create or replace function oshepherd_spike.can_upload_result(object_path text)
returns boolean language sql stable security definer set search_path = pg_catalog as $$
  select oshepherd_spike.jwt_role() = 'authenticated' and exists(
    select 1 from oshepherd_spike.jobs j join oshepherd_spike.workers w on w.id = j.worker_id
      join oshepherd_spike.memberships m on m.cluster_id = w.cluster_id and m.auth_subject = w.auth_subject
    where w.auth_subject = oshepherd_spike.jwt_subject() and w.revoked_at is null and m.revoked_at is null and m.role = 'worker'
      and j.generation = w.generation and j.session_id = w.session_id and j.state = 'running' and not j.stream
      and j.lease_until > pg_catalog.clock_timestamp() and j.execution_deadline > pg_catalog.clock_timestamp()
      and (j.mode = 'async' or j.consumer_until > pg_catalog.clock_timestamp())
      and object_path = j.cluster_id::text || '/' || j.id::text || '/' || j.attempt_id::text || '/result.json'
  )
$$;
-- The sole private-schema exception is this boolean policy predicate. It
-- cannot return rows, discover other subjects, mutate jobs, or renew leases.
grant usage on schema oshepherd_spike to authenticated;
revoke all on function oshepherd_spike.can_upload_result(text) from public,anon,service_role;
grant execute on function oshepherd_spike.can_upload_result(text) to authenticated;
reset role;
drop policy if exists oshepherd_spike_result_insert on storage.objects;
create policy oshepherd_spike_result_insert on storage.objects for insert to authenticated
  with check(bucket_id = 'oshepherd-spike-results' and oshepherd_spike.can_upload_result(name));
drop policy if exists oshepherd_spike_result_verify on storage.objects;
create policy oshepherd_spike_result_verify on storage.objects for select to authenticated
  using(bucket_id = 'oshepherd-spike-results' and oshepherd_spike.can_upload_result(name));
-- No authenticated UPDATE/DELETE policy: uploads are immutable, not upserts.
notify pgrst, 'reload schema';
commit;
