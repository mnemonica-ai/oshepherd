-- Private bounded SQL housekeeping. Scheduling is an explicit operator step;
-- this file does not create Cron jobs or mutate any existing schedules.
begin;
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_spike_schema_v1',0));
set local role oshepherd_spike_runtime;
create table if not exists oshepherd_spike.sweep_cursors (
  kind text primary key check (kind in ('reap','purge')),
  cluster_cursor uuid,
  last_run_at timestamptz
);
alter table oshepherd_spike.sweep_cursors enable row level security;
revoke all on oshepherd_spike.sweep_cursors from public,anon,authenticated,service_role;

create or replace function oshepherd_spike.scheduled_sweep(kind text)
returns integer language plpgsql security definer set search_path = pg_catalog
  set statement_timeout = '5s' set lock_timeout = '100ms' as $$
declare cursor_id uuid; cluster record; visited integer := 0;
begin
  if scheduled_sweep.kind not in ('reap','purge') or scheduled_sweep.kind is null then
    raise sqlstate 'PT422' using message = 'invalid_request'; end if;
  if not pg_catalog.pg_try_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_spike_sweep_'||scheduled_sweep.kind,0)) then return 0; end if;
  insert into oshepherd_spike.sweep_cursors(kind) values(scheduled_sweep.kind) on conflict do nothing;
  select cluster_cursor into cursor_id from oshepherd_spike.sweep_cursors s where s.kind = scheduled_sweep.kind for update;
  -- A keyset page bounds cluster work; locked clusters are retried on the next
  -- round rather than delaying unrelated admission/heartbeat transactions.
  for cluster in select id from oshepherd_spike.clusters
      where cursor_id is null or id > cursor_id order by id limit 4 for update skip locked loop
    if scheduled_sweep.kind = 'reap' then perform oshepherd_spike.reap(cluster.id,8);
    else perform oshepherd_spike.purge(cluster.id,8); end if;
    visited := visited+1; cursor_id := cluster.id;
  end loop;
  update oshepherd_spike.sweep_cursors s set cluster_cursor = case when visited < 4 then null else cursor_id end,
    last_run_at = pg_catalog.clock_timestamp() where s.kind = scheduled_sweep.kind;
  return visited;
end $$;
revoke all on function oshepherd_spike.scheduled_sweep(text) from public,anon,authenticated,service_role;
reset role;
commit;
