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
insert into oshepherd.release_migrations(version,sha256,fingerprint) values('20261010000200','b28f55aaa4fd922b6b3753e607485c019da1748b91ea7258a79aa65d05553784',oshepherd.schema_fingerprint());
