-- Fence non-key version/archive changes as well as physical deletion.
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_release_v1',0));
create or replace function oshepherd.verify_result_object()
returns trigger language plpgsql security definer set search_path=pg_catalog as $$
begin
  -- Lock the current, non-deleted metadata row through commit. JSON lookup
  -- accepts old Storage schemas without version/archive/delete-marker columns.
  perform 1 from storage.objects o
    where o.bucket_id=new.bucket_id and o.name=new.object_path
      and (pg_catalog.to_jsonb(o)->>'archived_at') is null
      and not coalesce((pg_catalog.to_jsonb(o)->>'is_delete_marker')::boolean,false)
    for share;
  if not found then raise foreign_key_violation using message='result_object_missing'; end if;
  return new;
end $$;
-- Release record. Hash covers the bytes preceding this marker.
insert into oshepherd.release_migrations(version,sha256,fingerprint) values('20261011000500','bb8017cb3c9d587e59058edaf7c0224511ace8bdba7db196a50fc233a1edfdc3',oshepherd.schema_fingerprint());
