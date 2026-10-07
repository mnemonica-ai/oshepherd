-- Only for the fixed, isolated local spike profile. Production installation
-- must merge schema exposure through its provider configuration adapter.
begin;
alter role authenticator set pgrst.db_schemas = 'public,graphql_public,oshepherd_spike_api';
notify pgrst, 'reload config';
notify pgrst, 'reload schema';
commit;
