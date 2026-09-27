-- =====================================================================
-- rls_test.sql — prove the rules actually fire
--
--     psql "$SUPABASE_DB_URL" -f supabase/tests/rls_test.sql
--
-- Run against a database that has the three migrations applied and the
-- seed loaded. Rolls back at the end, so it leaves nothing behind.
--
-- The point of this file: "role-based permissions" is a claim until
-- something tries to break in and fails. Each block below is an attempt
-- to do something the design forbids.
-- =====================================================================

begin;

set local role postgres;

-- Simulate a signed-in user by setting the JWT claims that app.role() and
-- app.lab_id() read. This is exactly what PostgREST does per request.
create or replace function _as(p_role text, p_lab_code text default null,
                              p_uid uuid default gen_random_uuid())
returns void language plpgsql as $$
declare lab uuid;
begin
  select id into lab from public.lab where code = p_lab_code;
  perform set_config('request.jwt.claims', json_build_object(
    'sub', p_uid,
    'role', 'authenticated',
    'app_metadata', json_build_object('lab_role', p_role, 'lab_id', lab)
  )::text, true);
  perform set_config('role', 'authenticated', true);
end;
$$;

-- A statement that matches no rows raises nothing, so a naive helper
-- reports "refused" when in truth it never attempted anything. That is a
-- test that always passes and tests nothing, so the row count is checked
-- too and a no-op is reported as NOT TESTED rather than ok.
create or replace function _expect_fail(sql text, label text)
returns void language plpgsql as $$
declare
  n integer;
begin
  begin
    execute sql;
    get diagnostics n = row_count;
    if n = 0 then
      raise warning 'NOT TESTED — % matched no rows; the test itself is wrong', label;
    else
      raise warning 'FAIL — % affected % row(s); it should have been refused', label, n;
    end if;
  exception
    when insufficient_privilege or check_violation or unique_violation then
      raise notice 'ok   — % refused (%)', label, sqlerrm;
    when raise_exception then
      raise notice 'ok   — % refused (%)', label, sqlerrm;
  end;
end;
$$;

-- RLS does not raise on a blocked DELETE: the rows are simply invisible,
-- so the statement affects zero rows. That IS the refusal, which the
-- helper above would report as NOT TESTED. This one checks the row is
-- still there afterwards, which is what actually matters.
create or replace function _expect_survives(del_sql text, check_sql text,
                                            label text)
returns void language plpgsql as $$
declare
  n integer;
  still boolean;
begin
  execute check_sql into still;
  if not still then
    raise warning 'NOT TESTED — % : the target row does not exist', label;
    return;
  end if;

  begin
    execute del_sql;
    get diagnostics n = row_count;
  exception
    when insufficient_privilege or raise_exception then
      raise notice 'ok   — % refused (%)', label, sqlerrm;
      return;
  end;

  execute check_sql into still;
  if still then
    raise notice 'ok   — % refused (RLS made the row invisible; % rows deleted, row intact)', label, n;
  else
    raise warning 'FAIL — % actually deleted the row', label;
  end if;
end;
$$;

-- =====================================================================
-- 1. Laboratory isolation
-- =====================================================================
select '--- 1. laboratory isolation ---' as section;

select _as('technician', 'RRSL-FBD');
select case
  when count(*) = 0 then 'FAIL — Faridabad technician sees no Faridabad rows'
  else 'ok   — Faridabad technician sees ' || count(*) || ' own-lab evaluations'
end from evaluation;

select case
  when count(*) > 0 then 'FAIL — Faridabad technician can read Mumbai rows'
  else 'ok   — Mumbai rows invisible to a Faridabad technician'
end
from evaluation e join lab l on l.id = e.lab_id where l.code = 'RRSL-MUM';

select _as('auditor');
select case
  when count(*) = 0 then 'FAIL — auditor sees nothing'
  else 'ok   — auditor sees all ' || count(*) || ' evaluations across labs'
end from evaluation;

-- =====================================================================
-- 2. A technician cannot approve their own work
-- =====================================================================
select '--- 2. separation of duties ---' as section;

select _as('technician', 'RRSL-FBD');
select _expect_fail($$
  update evaluation set status = 'approved'
  where ref = (select ref from evaluation
               where status = 'under_review' limit 1)
$$, 'technician approving an evaluation');

select _expect_fail($$
  update evaluation set status = 'issued'
  where ref = (select ref from evaluation where status = 'approved' limit 1)
$$, 'technician issuing a certificate');

-- =====================================================================
-- 3. Illegal state transitions
-- =====================================================================
select '--- 3. state machine ---' as section;

select _as('director');
select _expect_fail($$
  update evaluation set status = 'issued'
  where ref = (select ref from evaluation where status = 'draft' limit 1)
$$, 'draft jumping straight to issued');

-- =====================================================================
-- 4. An issued evaluation is frozen
-- =====================================================================
select '--- 4. immutability after issue ---' as section;

select _as('admin');
select _expect_fail($$
  update evaluation set zero_error_g = 999
  where ref = (select ref from evaluation where status = 'issued' limit 1)
$$, 'editing an issued evaluation');

-- Needs a second standard to switch TO, or the update is a no-op and
-- proves nothing.
insert into standard_version (id, title, config_path)
values ('OIML_R76-1_TEST', 'test-only second version', 'standards/test/')
on conflict (id) do nothing;

select _expect_fail($$
  update evaluation set standard_id = 'OIML_R76-1_TEST'
  where ref = (select ref from evaluation where status = 'draft' order by ref limit 1)
$$, 'changing the standard under a report');

-- =====================================================================
-- 5. Observations are append-only
-- =====================================================================
select '--- 5. append-only observations ---' as section;

select _as('technician', 'RRSL-FBD');

select _expect_fail($$
  update observation set indication_g = 12345
  where id = (select o.id from observation o
              join evaluation e on e.id = o.evaluation_id
              join lab l on l.id = e.lab_id
              where l.code = 'RRSL-FBD' limit 1)
$$, 'overwriting a recorded indication');

select _expect_survives(
  $$delete from observation where id = (select min(id) from observation)$$,
  $$select exists (select 1 from observation
                   where id = (select min(id) from observation))$$,
  'deleting an observation');

select _expect_fail($$
  insert into observation (evaluation_id, test_code, load_g, indication_g)
  select e.id, 'weighing_performance', 1000, 1000
  from evaluation e join lab l on l.id = e.lab_id
  where l.code = 'RRSL-FBD' and e.status = 'issued' limit 1
$$, 'recording an observation against an issued evaluation');

-- =====================================================================
-- 6. Audit log cannot be forged or erased
-- =====================================================================
select '--- 6. audit integrity ---' as section;

select _as('admin');
select _expect_fail($$
  insert into audit_log (action, table_name) values ('FORGED', 'evaluation')
$$, 'forging an audit entry');

-- seed.sql disables the audit triggers, so the log can be empty here and
-- the delete would match nothing -- which proves nothing. Put a row in
-- first, as superuser, then try to erase it as the signed-in role.
reset role;
insert into audit_log (action, table_name, row_id)
values ('INSERT', 'evaluation', 'test-row');
select _as('admin');

select _expect_survives(
  $$delete from audit_log where row_id = 'test-row'$$,
  $$select exists (select 1 from audit_log where row_id = 'test-row')$$,
  'erasing audit history');

-- =====================================================================
-- 7. Nobody can promote themselves
-- =====================================================================
select '--- 7. privilege escalation ---' as section;

-- The acting uid needs a real profile row, or the update matches nothing
-- and the policy is never exercised.
-- reset to superuser for the fixture: the previous _as() left us as
-- 'authenticated', which cannot write to auth.users
reset role;

do $$
declare u uuid := gen_random_uuid();
begin
  insert into auth.users (id) values (u);
  insert into profile (id, full_name, role, lab_id)
  values (u, 'Test Technician', 'technician',
          (select id from lab where code = 'RRSL-FBD'));
  perform set_config('request.jwt.claims', json_build_object(
    'sub', u, 'role', 'authenticated',
    'app_metadata', json_build_object('lab_role', 'technician',
      'lab_id', (select id from lab where code = 'RRSL-FBD'))
  )::text, true);
  perform set_config('role', 'authenticated', true);
end $$;

select _expect_fail($$
  update profile set role = 'director' where id = app.uid()
$$, 'technician promoting themselves to director');

-- =====================================================================
-- 8. Synthetic data can never carry a certificate
-- =====================================================================
select '--- 8. synthetic data guard ---' as section;

set local role postgres;
select _expect_fail($$
  update evaluation set certificate_no = 'FAKE/2026/001'
  where ref = (select ref from evaluation where is_synthetic order by ref limit 1)
$$, 'attaching a certificate number to synthetic data');

-- =====================================================================
select '--- summary ---' as section;
select 'every block above should read ok, and no FAIL should appear' as result;

rollback;
