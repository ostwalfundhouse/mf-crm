-- Only for local testing outside Supabase: a stand-in for Supabase's auth.jwt().
create schema if not exists auth;
create or replace function auth.jwt() returns jsonb language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true), '')::jsonb
$$;
