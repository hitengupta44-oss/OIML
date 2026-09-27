-- =====================================================================
-- security_test.sql — twelve break-in attempts, as a result table
--
--   Supabase SQL Editor -> New query -> paste -> Run
--
-- rls_test.sql reports through RAISE NOTICE, which psql prints and the
-- SQL Editor swallows. This version collects every result into a table
-- you can actually see, so it runs anywhere.
--
-- Read-only in effect: everything happens inside a transaction that
-- rolls back at the end. Nothing is left behind.
--
-- Every row must read PASS. A REFUSED verdict means the database
-- correctly stopped the attempt.
-- =====================================================================

begin;

create temporary table _result (
  n        int,
  category text,
  attempt  text,
  verdict  text,
  detail   text
);

-- The tests switch role to 'authenticated' to exercise the policies, and
-- that role needs to be able to write its own results.
grant all on _result to authenticated;

-- Simulate a signed-in user by setting the JWT claims that app.role()
-- and app.lab_id() read. This is exactly what PostgREST does per request.
create or replace function pg_temp._as(p_role text, p_lab_code text default null,
                                       p_uid uuid default gen_random_uuid())
returns void language plpgsql as $$
declare lab uuid;
begin
  select id into lab from public.lab where code = p_lab_code;
  perform set_config('request.jwt.claims', json_build_object(
    'sub', p_uid, 'role', 'authenticated',
    'app_metadata', json_build_object('lab_role', p_role, 'lab_id', lab)
  )::text, true);
  perform set_config('role', 'authenticated', true);
end;
$$;

-- An attempt that matches no rows raises nothing, so a naive check would
-- report "refused" when in truth nothing was attempted. The row count is
-- checked too, and a no-op is reported as NOT TESTED rather than a pass.
create or replace function pg_temp._try(p_n int, p_cat text, p_label text,
                                         p_sql text)
returns void language plpgsql as $$
declare rows_hit integer;
begin
  begin
    execute p_sql;
    get diagnostics rows_hit = row_count;
    if rows_hit = 0 then
      insert into _result values (p_n, p_cat, p_label, 'NOT TESTED',
        'matched no rows — the test itself is wrong');
    else
      insert into _result values (p_n, p_cat, p_label, 'FAIL',
        format('allowed, %s row(s) changed', rows_hit));
    end if;
  exception when others then
    insert into _result values (p_n, p_cat, p_label, 'PASS',
      'refused: ' || sqlerrm);
  end;
end;
$$;

-- RLS does not raise on a blocked DELETE: the rows are simply invisible,
-- so it affects zero rows. That IS the refusal. This checks the row
-- survived, which is what actually matters.
create or replace function pg_temp._survives(p_n int, p_cat text, p_label text,
                                              p_del text, p_check text)
returns void language plpgsql as $$
declare still boolean; rows_hit integer;
begin
  execute p_check into still;
  if not still then
    insert into _result values (p_n, p_cat, p_label, 'NOT TESTED',
      'target row does not exist');
    return;
  end if;
  begin
    execute p_del;
    get diagnostics rows_hit = row_count;
  exception when others then
    insert into _result values (p_n, p_cat, p_label, 'PASS',
      'refused: ' || sqlerrm);
    return;
  end;
  execute p_check into still;
  if still then
    insert into _result values (p_n, p_cat, p_label, 'PASS',
      'refused: RLS made the row invisible, row intact');
  else
    insert into _result values (p_n, p_cat, p_label, 'FAIL',
      'the row was actually deleted');
  end if;
end;
$$;

-- =====================================================================
-- 1. Laboratory isolation
-- =====================================================================

select pg_temp._as('technician', 'RRSL-FBD');

insert into _result
select 1, 'isolation', 'Faridabad technician sees own-lab evaluations',
       case when count(*) > 0 then 'PASS' else 'FAIL' end,
       count(*) || ' visible'
from evaluation;

insert into _result
select 2, 'isolation', 'Mumbai evaluations hidden from a Faridabad technician',
       case when count(*) = 0 then 'PASS' else 'FAIL' end,
       case when count(*) = 0 then 'none visible'
            else count(*) || ' LEAKED' end
from evaluation e join lab l on l.id = e.lab_id where l.code = 'RRSL-MUM';

select pg_temp._as('auditor');
insert into _result
select 3, 'isolation', 'auditor sees every laboratory',
       case when count(*) >= 100 then 'PASS' else 'FAIL' end,
       count(*) || ' visible across all labs'
from evaluation;

-- =====================================================================
-- 2. Separation of duties
-- =====================================================================

select pg_temp._as('technician', 'RRSL-FBD');

select pg_temp._try(4, 'duties', 'technician approves their own work', $sql$
  update evaluation set status = 'approved'
  where ref = (select ref from evaluation where status = 'under_review'
               order by ref limit 1)
$sql$);

select pg_temp._try(5, 'duties', 'technician issues a certificate', $sql$
  update evaluation set status = 'issued'
  where ref = (select ref from evaluation where status = 'approved'
               order by ref limit 1)
$sql$);

-- =====================================================================
-- 3. State machine
-- =====================================================================

select pg_temp._as('director');

select pg_temp._try(6, 'workflow', 'draft jumps straight to issued', $sql$
  update evaluation set status = 'issued'
  where ref = (select ref from evaluation where status = 'draft'
               order by ref limit 1)
$sql$);

-- =====================================================================
-- 4. Immutability
-- =====================================================================

select pg_temp._as('admin');

select pg_temp._try(7, 'immutability', 'editing an issued evaluation', $sql$
  update evaluation set zero_error_g = 999
  where ref = (select ref from evaluation where status = 'issued'
               order by ref limit 1)
$sql$);

reset role;
insert into standard_version (id, title, config_path)
values ('OIML_R76-1_TEST', 'test-only second version', 'standards/test/')
on conflict (id) do nothing;
select pg_temp._as('admin');

select pg_temp._try(8, 'immutability', 'changing the standard under a report', $sql$
  update evaluation set standard_id = 'OIML_R76-1_TEST'
  where ref = (select ref from evaluation where status = 'draft'
               order by ref limit 1)
$sql$);

-- =====================================================================
-- 5. Append-only observations
-- =====================================================================

select pg_temp._as('technician', 'RRSL-FBD');

select pg_temp._try(9, 'append-only', 'overwriting a recorded indication', $sql$
  update observation set indication_g = 12345
  where id = (select min(id) from observation)
$sql$);

select pg_temp._survives(10, 'append-only', 'deleting an observation',
  $sql$delete from observation where id = (select min(id) from observation)$sql$,
  $sql$select exists (select 1 from observation
                      where id = (select min(id) from observation))$sql$);

select pg_temp._try(11, 'append-only', 'recording against an issued evaluation', $sql$
  insert into observation (evaluation_id, test_code, sequence_no, load_g, indication_g)
  select e.id, 'weighing_performance', 99999, 1000, 1000
  from evaluation e where e.status = 'issued' order by e.ref limit 1
$sql$);

-- =====================================================================
-- 6. Privilege escalation
-- =====================================================================

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

select pg_temp._try(12, 'escalation', 'technician promotes themselves to director', $sql$
  update profile set role = 'director' where id = app.uid()
$sql$);

-- =====================================================================
-- 7. Synthetic-data guard
-- =====================================================================

reset role;

select pg_temp._try(13, 'integrity', 'certificate number on synthetic data', $sql$
  update evaluation set certificate_no = 'FAKE/2026/001'
  where ref = (select ref from evaluation where is_synthetic
               order by ref limit 1)
$sql$);

-- =====================================================================
-- results
-- =====================================================================

select n as "#", category, attempt, verdict, detail
from _result
union all
select 99, '', '── SUMMARY ──',
       case when count(*) filter (where verdict <> 'PASS') = 0
            then 'ALL PASS' else 'SEE ABOVE' end,
       count(*) filter (where verdict = 'PASS') || ' of ' || count(*) ||
       ' attempts correctly refused'
from _result
order by 1;

rollback;
