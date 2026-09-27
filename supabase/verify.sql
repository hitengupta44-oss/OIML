-- =====================================================================
-- verify.sql — did Stage 1 land?
--
-- Runs as ONE query so the Supabase SQL Editor shows every result at once.
-- No psql meta-commands, so it works in the editor and in psql alike.
--
--   SQL Editor -> New query -> paste -> Run
--
-- Read-only. Each row reads ok or FAIL.
-- Before seeding, checks 10 and 12 will show zeros and a missing-accounts
-- FAIL. That is expected. Anything else marked FAIL should be fixed first.
-- =====================================================================

with checks as (

-- 1 -------------------------------------------------------------------
select 1 as n, 'tables' as check_name,
  case when count(*) = 10 then 'ok' else 'FAIL' end as result,
  count(*) || ' of 10 present' as detail
from information_schema.tables
where table_schema = 'public'
  and table_name in ('lab','standard_version','profile','manufacturer',
                     'instrument_model','evaluation','observation',
                     'attachment','report_version','audit_log')

-- 2 -------------------------------------------------------------------
-- A table with RLS off is open to any signed-in user whatever the
-- policies say. This is the check that matters most here.
union all
select 2, 'row-level security',
  case when count(*) = 0 then 'ok' else 'FAIL' end,
  coalesce('RLS OFF on ' || string_agg(c.relname, ', '), 'enabled on all tables')
from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
where ns.nspname = 'public' and c.relkind = 'r'
  and c.relname in ('lab','standard_version','profile','manufacturer',
                    'instrument_model','evaluation','observation',
                    'attachment','report_version','audit_log')
  and not c.relrowsecurity

-- 3 -------------------------------------------------------------------
union all
select 3, 'policies',
  case when count(*) >= 20 then 'ok' else 'FAIL' end,
  count(*) || ' policies across ' || count(distinct tablename) || ' tables'
from pg_policies where schemaname = 'public'

union all
select 4, 'audit log is append-only',
  case when count(*) filter (where cmd <> 'SELECT') = 0 then 'ok' else 'FAIL' end,
  case when count(*) filter (where cmd <> 'SELECT') = 0
       then 'no client session can forge or erase history'
       else 'a write policy exists — history could be forged' end
from pg_policies where schemaname = 'public' and tablename = 'audit_log'

-- 5 -------------------------------------------------------------------
-- Checked by name rather than by count: a count only tells you the number
-- is different, never which one is missing.
union all
select 5, 'guard and audit triggers',
  case when count(*) = 6 then 'ok' else 'FAIL' end,
  case when count(*) = 6 then 'all 6 present'
       else 'missing: ' || (
         select string_agg(w, ', ') from unnest(array[
           'evaluation_guard','observation_guard','report_version_guard',
           'audit_evaluation','audit_observation','audit_report_version']) w
         where w not in (select t2.tgname from pg_trigger t2
                         where not t2.tgisinternal)) end
from pg_trigger t join pg_class c on c.oid = t.tgrelid
where not t.tgisinternal
  and t.tgname in ('evaluation_guard','observation_guard','report_version_guard',
                   'audit_evaluation','audit_observation','audit_report_version')

-- 6 -------------------------------------------------------------------
union all
select 6, 'app.* helper functions',
  case when count(*) = 13 then 'ok' else 'FAIL' end,
  case when count(*) = 13 then 'all 13 present'
       else count(*) || ' of 13 — missing: ' || (
         select string_agg(w, ', ') from unnest(array[
           'role','lab_id','uid','has_role','transition_allowed','role_may_set',
           'guard_evaluation_update','guard_observation_write',
           'guard_report_version','next_report_version','next_ref',
           'approval_mark','write_audit']) w
         where w not in (select p2.proname from pg_proc p2
                         join pg_namespace n2 on n2.oid = p2.pronamespace
                         where n2.nspname = 'app')) end
from pg_proc p join pg_namespace ns on ns.oid = p.pronamespace
where ns.nspname = 'app'
  and p.proname in ('role','lab_id','uid','has_role','transition_allowed',
                    'role_may_set','guard_evaluation_update',
                    'guard_observation_write','guard_report_version',
                    'next_report_version','next_ref','approval_mark','write_audit')

-- 7 -------------------------------------------------------------------
-- Without security_invoker a view runs with the definer's rights and
-- silently bypasses every policy on its base tables.
union all
select 7, 'views use security_invoker',
  case when count(*) = 3 then 'ok' else 'FAIL' end,
  count(*) || ' of 3 views'
from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
where ns.nspname = 'public' and c.relkind = 'v'
  and c.relname in ('v_evaluation_summary','v_dashboard_counts','v_observation_live')
  and 'security_invoker=true' = any(c.reloptions)

-- 8 -------------------------------------------------------------------
union all
select 8, 'laboratories',
  case when count(*) = 8 then 'ok' else 'FAIL' end,
  count(*) || ' RRSLs'
from lab

union all
select 9, 'default standard version',
  case when count(*) = 1 then 'ok' else 'FAIL' end,
  coalesce(string_agg(id, ', '), 'none')
from standard_version where is_default

-- 10 ------------------------------------------------------------------
union all
select 10, 'synthetic-data constraint',
  case when count(*) = 1 then 'ok' else 'FAIL' end,
  case when count(*) = 1 then 'synthetic rows cannot carry a certificate'
       else 'no_certificate_for_synthetic is missing' end
from pg_constraint where conname = 'no_certificate_for_synthetic'

-- 11 ------------------------------------------------------------------
-- n = Max/e is computed by the database so it can never drift.
union all
select 11, 'n is a generated column',
  case when count(*) = 1 then 'ok' else 'FAIL' end,
  case when count(*) = 1 then 'n = Max/e computed by the database'
       else 'n is a plain column and can drift' end
from information_schema.columns
where table_name = 'instrument_model' and column_name = 'n'
  and is_generated = 'ALWAYS'

-- 12 ------------------------------------------------------------------
union all
select 12, 'data loaded', 'info',
  (select count(*) from evaluation) || ' evaluations, ' ||
  (select count(*) from observation) || ' observations, ' ||
  (select count(*) from instrument_model) || ' models, ' ||
  (select count(*) from manufacturer) || ' manufacturers'

union all
select 13, 'no synthetic certificate',
  case when count(*) = 0 then 'ok' else 'FAIL' end,
  case when count(*) = 0 then 'clean'
       else count(*) || ' synthetic row(s) carry a certificate' end
from evaluation
where is_synthetic and (certificate_no is not null or gazette_date is not null)

-- 14 ------------------------------------------------------------------
union all
select 14, 'accounts', 'info',
  coalesce((select string_agg(role || '=' || c, ', ' order by role)
            from (select role::text as role, count(*) as c
                  from profile group by role) s),
           'none yet — run supabase/seed/create_users.py')

-- 15 ------------------------------------------------------------------
-- Roles must live in app_metadata. A role in user_metadata is
-- self-issued: any user can edit that through the client API.
union all
select 15, 'roles in app_metadata',
  case when (select count(*) from auth.users) = 0 then 'pending'
       when count(*) = 0 then 'ok' else 'FAIL' end,
  case when (select count(*) from auth.users) = 0 then 'no accounts yet'
       when count(*) = 0 then 'every account carries app_metadata.lab_role'
       else count(*) || ' account(s) missing app_metadata.lab_role' end
from auth.users where raw_app_meta_data -> 'lab_role' is null

)
select n as "#", check_name as "check", result, detail
from checks order by n;
