-- Only for the fixed, isolated local spike profile. Preserve other installed
-- APIs when a receipt-upgrade test reapplies this mutable development spike.
begin;
do $exposure$ declare global_cfg text; database_cfg text; merged text; changed boolean:=false;
begin
  select substr(setting,18) into global_cfg from pg_catalog.pg_db_role_setting s
    cross join lateral unnest(s.setconfig) setting
    where s.setrole=(select oid from pg_roles where rolname='authenticator') and s.setdatabase=0
      and setting like 'pgrst.db_schemas=%';
  select substr(setting,18) into database_cfg from pg_catalog.pg_db_role_setting s
    cross join lateral unnest(s.setconfig) setting
    where s.setrole=(select oid from pg_roles where rolname='authenticator')
      and s.setdatabase=(select oid from pg_database where datname=current_database())
      and setting like 'pgrst.db_schemas=%';
  select string_agg(name,',' order by first_position) into merged from (
    select btrim(name) name,min(position) first_position from unnest(
      string_to_array(coalesce(database_cfg,global_cfg,'public,graphql_public'),',') ||
      string_to_array(coalesce(global_cfg,''),',') || array['oshepherd_spike_api'])
      with ordinality names(name,position) where btrim(name)<>'' group by btrim(name)) schemas;
  if global_cfg is distinct from merged then
    execute format('alter role authenticator set pgrst.db_schemas=%L',merged); changed:=true; end if;
  if database_cfg is not null and database_cfg is distinct from merged then
    execute format('alter role authenticator in database %I set pgrst.db_schemas=%L',current_database(),merged); changed:=true; end if;
  if changed then perform pg_notify('pgrst','reload config'); perform pg_notify('pgrst','reload schema'); end if;
end $exposure$;
commit;
