-- Upgrade both supported baselines without altering provider-owned tables.
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_release_v1',0));
-- Modern Storage versions objects by (bucket_id,name,version), so a path is
-- not a foreign-key target. Keep authoritative commit checks in our schema.
alter table oshepherd.result_objects drop constraint if exists result_objects_bucket_id_object_path_fkey;
grant usage on schema storage to oshepherd_runtime;
grant select on storage.objects to oshepherd_runtime;
-- Runtime metadata reads are limited to this installation's private bucket.
-- Preserve the original authenticated worker predicate.
alter policy oshepherd_result_verify on storage.objects to authenticated,oshepherd_runtime
  using(bucket_id='oshepherd-results' and
    (current_user='oshepherd_runtime' or oshepherd.can_upload_result(name)));

set local role oshepherd_runtime;
create function oshepherd.verify_result_object()
returns trigger language plpgsql security definer set search_path=pg_catalog as $$
begin
  -- Lock the current, non-deleted metadata row through commit. JSON lookup
  -- accepts old Storage schemas without version/archive/delete-marker columns.
  perform 1 from storage.objects o
    where o.bucket_id=new.bucket_id and o.name=new.object_path
      and (pg_catalog.to_jsonb(o)->>'archived_at') is null
      and not coalesce((pg_catalog.to_jsonb(o)->>'is_delete_marker')::boolean,false)
    for key share;
  if not found then raise foreign_key_violation using message='result_object_missing'; end if;
  return new;
end $$;
revoke all on function oshepherd.verify_result_object() from public,anon,authenticated,service_role;
create trigger verify_result_object before insert or update on oshepherd.result_objects
  for each row execute function oshepherd.verify_result_object();
reset role;
-- Only this fixed trigger needs a row lock; do not grant UPDATE on provider
-- metadata to the runtime. postgres already owns the installation operation.
alter function oshepherd.verify_result_object() owner to postgres;
notify pgrst,'reload schema';

-- Release record. Hash covers the bytes preceding this marker.
insert into oshepherd.release_migrations(version,sha256,fingerprint) values('20261011000200','88dd62fa13fc9dd77d89182688dc7cd8d3b054622b6ffc8e6e84076f1e62b5fe',oshepherd.schema_fingerprint());
