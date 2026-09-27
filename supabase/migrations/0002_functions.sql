-- =====================================================================
-- 0002_functions.sql — the rules, enforced in the database
--
-- Everything here exists because a legal-metrology record that can be
-- silently altered is worthless. Hiding a button in the UI is not
-- enforcement; a trigger is.
-- =====================================================================

create schema if not exists app;

-- Without these, every RLS policy fails: the policies call app.has_role()
-- and app.lab_id(), and a role that cannot reach the app schema cannot
-- execute them, so "permission denied for schema app" comes back instead
-- of a row. Supabase grants usage on public automatically; a custom
-- schema needs saying explicitly.
grant usage on schema app to authenticated, anon, service_role;

-- ---------------------------------------------------------------------
-- JWT helpers
--
-- Roles live in app_metadata, never user_metadata — users can edit the
-- latter themselves, which would make role checks meaningless.
-- ---------------------------------------------------------------------

create or replace function app.role()
returns lab_role language sql stable as $$
  select coalesce(
    nullif(current_setting('request.jwt.claims', true), '')::jsonb
      -> 'app_metadata' ->> 'lab_role',
    'auditor'
  )::lab_role;
$$;

create or replace function app.lab_id()
returns uuid language sql stable as $$
  select nullif(
    nullif(current_setting('request.jwt.claims', true), '')::jsonb
      -> 'app_metadata' ->> 'lab_id',
    ''
  )::uuid;
$$;

create or replace function app.uid()
returns uuid language sql stable as $$
  select nullif(
    nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub',
    ''
  )::uuid;
$$;

create or replace function app.has_role(variadic roles lab_role[])
returns boolean language sql stable as $$
  select app.role() = any(roles);
$$;

-- ---------------------------------------------------------------------
-- state machine (Approval of Models Rules, 2011, Rules 7 to 14)
-- ---------------------------------------------------------------------

create or replace function app.transition_allowed(
  from_status evaluation_status,
  to_status   evaluation_status
) returns boolean language sql immutable as $$
  select (from_status, to_status) in (
    ('draft','submitted'), ('draft','cancelled'),
    ('submitted','under_review'), ('submitted','draft'),
    ('under_review','approved'), ('under_review','rejected'), ('under_review','draft'),
    ('approved','issued'),
    ('issued','suspended'), ('issued','revoked'), ('issued','amended'),
    ('rejected','resubmitted'),
    ('resubmitted','submitted'),
    ('suspended','issued'), ('suspended','revoked'),
    ('amended','issued')
  );
$$;

create or replace function app.role_may_set(
  to_status evaluation_status
) returns boolean language sql stable as $$
  select case to_status
    when 'submitted'    then app.has_role('technician','admin')
    when 'draft'        then app.has_role('technician','reviewer','admin')
    when 'under_review' then app.has_role('reviewer','admin')
    when 'approved'     then app.has_role('approver','admin')
    when 'rejected'     then app.has_role('reviewer','approver','admin')
    when 'issued'       then app.has_role('director','admin')
    when 'suspended'    then app.has_role('director','admin')
    when 'revoked'      then app.has_role('director','admin')
    when 'amended'      then app.has_role('approver','director','admin')
    when 'resubmitted'  then app.has_role('technician','admin')
    when 'cancelled'    then app.has_role('technician','reviewer','admin')
    else false
  end;
$$;

create or replace function app.guard_evaluation_update()
returns trigger language plpgsql as $$
begin
  -- An issued evaluation is frozen. Corrections go through a formal
  -- amendment, which creates a new report version.
  if old.status = 'issued'
     and new.status not in ('suspended','revoked','amended') then
    raise exception
      'evaluation % is issued and cannot be edited (Approval of Models Rules, Rule 11(2))',
      old.ref using errcode = 'check_violation';
  end if;

  -- The standard that judged a report can never change under it.
  if new.standard_id is distinct from old.standard_id then
    raise exception
      'standard_id is immutable; create a new evaluation to test against another standard'
      using errcode = 'check_violation';
  end if;

  if new.ref is distinct from old.ref then
    raise exception 'ref is immutable' using errcode = 'check_violation';
  end if;

  if new.status is distinct from old.status then
    if not app.transition_allowed(old.status, new.status) then
      raise exception 'transition % -> % is not permitted', old.status, new.status
        using errcode = 'check_violation';
    end if;
    if not app.role_may_set(new.status) then
      raise exception 'role % may not set status %', app.role(), new.status
        using errcode = 'insufficient_privilege';
    end if;

    if new.status = 'submitted'   then new.submitted_at := now(); end if;
    if new.status = 'under_review' then new.reviewed_by  := app.uid(); end if;
    if new.status = 'approved'    then new.approved_by  := app.uid(); end if;
    if new.status = 'issued'      then new.issued_at    := now(); end if;
  end if;

  new.updated_at := now();
  return new;
end;
$$;

create trigger evaluation_guard
  before update on evaluation
  for each row execute function app.guard_evaluation_update();

-- ---------------------------------------------------------------------
-- observations are append-only
-- ---------------------------------------------------------------------

create or replace function app.guard_observation_write()
returns trigger language plpgsql as $$
declare
  st evaluation_status;
begin
  if tg_op = 'DELETE' then
    raise exception 'observations cannot be deleted; supersede the row instead'
      using errcode = 'insufficient_privilege';
  end if;

  if tg_op = 'UPDATE' then
    -- The only permitted update is marking a row superseded.
    if new.evaluation_id  is distinct from old.evaluation_id
    or new.test_code      is distinct from old.test_code
    or new.load_g         is distinct from old.load_g
    or new.indication_g   is distinct from old.indication_g
    or new.delta_load_g   is distinct from old.delta_load_g
    or new.recorded_by    is distinct from old.recorded_by
    or new.recorded_at    is distinct from old.recorded_at then
      raise exception
        'observations are append-only; insert a corrected row and set superseded_by'
        using errcode = 'insufficient_privilege';
    end if;
    if old.superseded_by is not null then
      raise exception 'observation % is already superseded', old.id
        using errcode = 'check_violation';
    end if;
    return new;
  end if;

  -- INSERT: only into an evaluation that is still open for data entry.
  select status into st from evaluation where id = new.evaluation_id;
  if st is null then
    raise exception 'unknown evaluation %', new.evaluation_id;
  end if;
  if st not in ('draft','resubmitted') then
    raise exception
      'evaluation is % ; observations may only be recorded while draft or resubmitted', st
      using errcode = 'check_violation';
  end if;

  new.recorded_by := coalesce(new.recorded_by, app.uid());
  return new;
end;
$$;

create trigger observation_guard
  before insert or update or delete on observation
  for each row execute function app.guard_observation_write();

-- ---------------------------------------------------------------------
-- report versions are immutable once written
-- ---------------------------------------------------------------------

create or replace function app.guard_report_version()
returns trigger language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'report versions cannot be deleted'
      using errcode = 'insufficient_privilege';
  end if;
  -- Signing is the one permitted update.
  if new.payload_sha256 is distinct from old.payload_sha256
  or new.payload        is distinct from old.payload
  or new.version        is distinct from old.version
  or new.standard_id    is distinct from old.standard_id then
    raise exception 'a rendered report version is immutable; render a new version'
      using errcode = 'insufficient_privilege';
  end if;
  return new;
end;
$$;

create trigger report_version_guard
  before update or delete on report_version
  for each row execute function app.guard_report_version();

create or replace function app.next_report_version(eval uuid)
returns int language sql stable as $$
  select coalesce(max(version), 0) + 1 from report_version where evaluation_id = eval;
$$;

-- ---------------------------------------------------------------------
-- laboratory reference number:  RRSL-FBD/NAWI/2026/0042
-- ---------------------------------------------------------------------

create or replace function app.next_ref(p_lab uuid, p_year int default null)
returns text language plpgsql stable as $$
declare
  code text; yr int; seq int;
begin
  select l.code into code from lab l where l.id = p_lab;
  if code is null then raise exception 'unknown lab %', p_lab; end if;
  yr := coalesce(p_year, extract(year from now())::int);
  select coalesce(max(split_part(e.ref, '/', 4)::int), 0) + 1
    into seq
    from evaluation e
   where e.lab_id = p_lab
     and e.ref like code || '/NAWI/' || yr || '/%';
  return format('%s/NAWI/%s/%s', code, yr, lpad(seq::text, 4, '0'));
end;
$$;

-- Approval mark, Rule 12(1): IND / last two digits of the year of issue /
-- laboratory code / model code.
create or replace function app.approval_mark(p_lab uuid, p_model_code int,
                                             p_year int default null)
returns text language plpgsql stable as $$
declare
  mc text; yr int;
begin
  select l.mark_code into mc from lab l where l.id = p_lab;
  yr := coalesce(p_year, extract(year from now())::int);
  return format('IND/%s/%s/%s', right(yr::text, 2), mc, lpad(p_model_code::text, 2, '0'));
end;
$$;

-- ---------------------------------------------------------------------
-- audit log
-- ---------------------------------------------------------------------

create or replace function app.write_audit()
returns trigger language plpgsql security definer set search_path = public, app as $fn$
declare
  eval uuid;
  rid  text;
  act  text;
  who  uuid;
begin
  -- IF branches, not a CASE: a CASE resolves its type across ALL branches
  -- at plan time, and new.id is uuid on evaluation but bigint on
  -- observation, which fails with "CASE types uuid and bigint cannot be
  -- matched".
  if tg_table_name = 'evaluation' then
    eval := coalesce(new.id, old.id);
  else
    eval := coalesce(new.evaluation_id, old.evaluation_id);
  end if;

  rid := coalesce(new.id, old.id)::text;

  -- Nested, not one boolean: PL/pgSQL compiles a whole condition into a
  -- single SQL expression, so "... and new.status is distinct from ..."
  -- resolves new.status even on tables that have no such column.
  act := tg_op;
  if tg_op = 'UPDATE' and tg_table_name = 'evaluation' then
    if new.status is distinct from old.status then
      act := 'STATUS';
    end if;
  end if;

  begin
    who := app.uid();
  exception when others then
    who := null;
  end;

  insert into audit_log (actor, actor_role, action, table_name, row_id,
                         evaluation_id, before, after)
  values (who, app.role(), act, tg_table_name, rid, eval,
          case when tg_op = 'INSERT' then null else to_jsonb(old) end,
          case when tg_op = 'DELETE' then null else to_jsonb(new) end);

  return coalesce(new, old);
end;
$fn$;

create trigger audit_evaluation
  after insert or update on evaluation
  for each row execute function app.write_audit();

create trigger audit_observation
  after insert or update on observation
  for each row execute function app.write_audit();

create trigger audit_report_version
  after insert or update on report_version
  for each row execute function app.write_audit();

-- ---------------------------------------------------------------------
-- dashboard views
-- ---------------------------------------------------------------------

create or replace view v_evaluation_summary as
select
  e.id, e.ref, e.status, e.verdict, e.jurisdiction, e.standard_id,
  e.is_synthetic, e.created_at, e.issued_at, e.approval_mark,
  l.code as lab_code, l.name as lab_name,
  m.name as manufacturer, im.model, im.instrument_type,
  im.accuracy_class, im.max_capacity_g, im.e_g, im.n,
  (select count(*) from observation o
     where o.evaluation_id = e.id and o.superseded_by is null) as observation_count,
  (select count(*) from attachment a where a.evaluation_id = e.id) as attachment_count,
  (select max(version) from report_version r where r.evaluation_id = e.id) as latest_report_version
from evaluation e
join lab l               on l.id = e.lab_id
join instrument_model im on im.id = e.model_id
join manufacturer m      on m.id = im.manufacturer_id;

create or replace view v_dashboard_counts as
select
  l.code as lab_code, l.name as lab_name,
  count(*)                                              as total,
  count(*) filter (where e.status = 'draft')            as draft,
  count(*) filter (where e.status = 'under_review')     as under_review,
  count(*) filter (where e.status = 'approved')         as approved,
  count(*) filter (where e.status = 'issued')           as issued,
  count(*) filter (where e.status = 'rejected')         as rejected,
  count(*) filter (where e.verdict = 'PASS')            as passing,
  count(*) filter (where e.verdict = 'FAIL')            as failing,
  count(*) filter (where e.created_at > now() - interval '30 days') as last_30_days
from evaluation e
join lab l on l.id = e.lab_id
group by l.code, l.name;

-- Live observations only: superseded rows stay in the table for the audit
-- trail but never reach a report.
create or replace view v_observation_live as
select * from observation where superseded_by is null;


-- ---------------------------------------------------------------------
-- execute rights on everything defined above
-- ---------------------------------------------------------------------
grant execute on all functions in schema app to authenticated, anon, service_role;
alter default privileges in schema app
  grant execute on functions to authenticated, anon, service_role;
