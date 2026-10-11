-- Include result commit triggers and runtime Storage grants in drift detection.
select pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oshepherd_release_v1',0));
set local role oshepherd_runtime;
create or replace function oshepherd.schema_fingerprint()
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
  'triggers',(select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(c.relname,t.tgname,t.tgenabled,pg_catalog.pg_get_triggerdef(t.oid)) order by c.relname,t.tgname)
    from pg_catalog.pg_trigger t join pg_catalog.pg_class c on c.oid=t.tgrelid join pg_catalog.pg_namespace n on n.oid=c.relnamespace
    where n.nspname='oshepherd' and not t.tgisinternal),
  'storage_read_grants',pg_catalog.jsonb_build_array(
    pg_catalog.has_schema_privilege('oshepherd_runtime','storage','USAGE'),
    pg_catalog.has_table_privilege('oshepherd_runtime','storage.objects','SELECT'),
    pg_catalog.has_table_privilege('oshepherd_runtime','storage.objects','UPDATE')),
  'role',(select pg_catalog.jsonb_build_array(rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls)
    from pg_catalog.pg_roles where rolname='oshepherd_runtime'),
  'role_members',(select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(m.member,m.admin_option) order by m.member)
    from pg_catalog.pg_auth_members m join pg_catalog.pg_roles r on r.oid=m.roleid where r.rolname='oshepherd_runtime')
 )::text,'UTF8'),'sha256'),'hex')
$$;
revoke all on function oshepherd.schema_fingerprint() from public,anon,authenticated,service_role;
reset role;

-- Release record. Hash covers the bytes preceding this marker.
insert into oshepherd.release_migrations(version,sha256,fingerprint) values('20261011000300','a739d609fe4502497ddbb0e10a3e4ccbaa17a78954cbcfaf1cb30fcef47d4343',oshepherd.schema_fingerprint());
