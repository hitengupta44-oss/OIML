-- =====================================================================
-- RESET.sql — wipe the NAWI schema and start clean
--
-- Keeps the project, so your URL, anon key, service key and JWT secret
-- all stay valid. Only the application schema goes.
--
-- Run this, then setup.sql, then seed.sql ONCE.
--
-- Safe: it touches only the public and app schemas. Supabase's own
-- machinery lives in auth, storage, realtime and extensions, none of
-- which are named here. Your accounts under Authentication survive.
--
-- Everything in public right now is synthetic seed data, so nothing of
-- value is lost. If that is ever untrue, take a backup first.
-- =====================================================================

drop schema if exists app cascade;
drop schema if exists public cascade;

create schema public;

-- Restore the grants Supabase sets up on a fresh project. Without these,
-- PostgREST cannot reach anything the migrations go on to create.
grant usage on schema public to postgres, anon, authenticated, service_role;
grant all   on schema public to postgres, service_role;

alter default privileges in schema public
  grant all on tables    to postgres, anon, authenticated, service_role;
alter default privileges in schema public
  grant all on functions to postgres, anon, authenticated, service_role;
alter default privileges in schema public
  grant all on sequences to postgres, anon, authenticated, service_role;

-- pgcrypto and pg_trgm live in the extensions schema on Supabase; the
-- migrations re-create them if they are missing.

-- ---------------------------------------------------------------------
-- must return zero rows
-- ---------------------------------------------------------------------
select table_name
from information_schema.tables
where table_schema = 'public'
order by table_name;
