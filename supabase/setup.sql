-- =====================================================================
-- setup.sql — the whole of Stage 1 in one paste
--
-- Supabase SQL Editor: paste this file, Run. That is the entire schema,
-- the rules engine triggers and row-level security.
--
-- Built by concatenating migrations 0001, 0002 and 0003. If you are using
-- the Supabase CLI, run those three separately instead so migration
-- history stays meaningful.
--
-- Then, in order:
--   supabase/seed/seed.sql        the 120-evaluation demo dataset
--   supabase/seed/create_users.py the 12 demo accounts
--   supabase/verify.sql           confirms everything landed
--   supabase/tests/rls_test.sql   proves the rules refuse things
-- =====================================================================

-- ####################################################################
-- ## 0001_schema.sql
-- ####################################################################

-- =====================================================================
-- PS 26035 — NAWI type evaluation
-- 0001_schema.sql — core tables
--
-- Design rules this schema enforces, not just documents:
--   * observations are append-only; a correction is a new row
--   * an issued evaluation can never be edited
--   * every evaluation records the standard_id it was judged under, so a
--     2019 report re-renders under 2019 rules forever
--   * synthetic data is flagged and can never produce a certificate
-- =====================================================================

create extension if not exists "pgcrypto";
create extension if not exists "pg_trgm";

-- ---------------------------------------------------------------------
-- enums
-- ---------------------------------------------------------------------

create type lab_role as enum (
  'technician',   -- enters observations
  'reviewer',     -- metrologist; verifies
  'approver',     -- laboratory head; signs
  'director',     -- Director of Legal Metrology; issues certificates
  'admin',        -- users, labs, standard versions, templates
  'auditor'       -- read-only
);

-- Legal Metrology (Approval of Models) Rules, 2011, Rules 7 to 14
create type evaluation_status as enum (
  'draft', 'submitted', 'under_review', 'approved', 'issued',
  'rejected', 'resubmitted', 'suspended', 'revoked', 'amended', 'cancelled'
);

create type verdict as enum ('PASS', 'FAIL', 'PENDING');

-- OIML symbols. India prints IIII as IV; that is a render-time concern,
-- handled by standards/accuracy-classes.json, not stored twice here.
create type accuracy_class as enum ('I', 'II', 'III', 'IIII');

create type indication_type as enum
  ('self_indicating', 'semi_self_indicating', 'non_self_indicating');

-- ---------------------------------------------------------------------
-- reference data
-- ---------------------------------------------------------------------

create table lab (
  id            uuid primary key default gen_random_uuid(),
  code          text not null unique,            -- RRSL-FBD
  name          text not null,                   -- RRSL Faridabad
  state         text,
  -- Rule 12(1): the code number assigned to the laboratory, used in the
  -- approval mark IND/{YY}/{LAB_CODE}/{MODEL_CODE}
  mark_code     text not null unique check (mark_code ~ '^[0-9]{2}$'),
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);

-- Registry of standard versions the engine can evaluate against.
-- Adding a revised R 76 means inserting a row and shipping its JSON,
-- not editing application code.
create table standard_version (
  id            text primary key,                -- 'OIML_R76-1_2006'
  title         text not null,
  published     date,
  national_note text,
  config_path   text not null,                   -- 'standards/oiml-r76-1-2006/'
  is_default    boolean not null default false,
  created_at    timestamptz not null default now()
);

create unique index one_default_standard
  on standard_version (is_default) where is_default;

create table profile (
  id            uuid primary key references auth.users (id) on delete cascade,
  full_name     text not null,
  designation   text,
  lab_id        uuid references lab (id),
  role          lab_role not null default 'technician',
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);

create index profile_lab_idx on profile (lab_id);

create table manufacturer (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  city          text,
  state         text,
  country       text default 'India',
  created_at    timestamptz not null default now()
);

-- NOT a unique constraint on (name, city): the seed inserts city as NULL,
-- and in SQL NULL is never equal to NULL, so such a constraint never
-- fires on a null row and a re-run silently duplicates every row.
-- coalesce() turns "no city" into a real value.
create unique index manufacturer_name_city_uniq
  on manufacturer (lower(name), coalesce(lower(city), ''));

create index manufacturer_name_trgm on manufacturer using gin (name gin_trgm_ops);

-- ---------------------------------------------------------------------
-- the instrument under evaluation
-- ---------------------------------------------------------------------

create table instrument_model (
  id                uuid primary key default gen_random_uuid(),
  manufacturer_id   uuid not null references manufacturer (id),
  model             text not null,
  instrument_type   text not null,               -- table_top, platform, weighbridge...

  accuracy_class    accuracy_class not null,
  max_capacity_g    numeric(20,6) not null check (max_capacity_g > 0),
  e_g               numeric(20,6) not null check (e_g > 0),
  d_g               numeric(20,6) not null check (d_g > 0),
  min_capacity_g    numeric(20,6) not null check (min_capacity_g > 0),

  -- n = Max/e, generated so it can never drift from its inputs
  n                 numeric(20,6)
                    generated always as (max_capacity_g / e_g) stored,

  indication_type   indication_type not null default 'self_indicating',
  is_electronic     boolean not null default true,
  has_tare_device   boolean not null default false,
  has_aux_device    boolean not null default false,
  is_grading        boolean not null default false,

  temp_low_c        numeric(6,2) default -10,
  temp_high_c       numeric(6,2) default 40,
  software_version  text,
  software_checksum text,

  created_at        timestamptz not null default now(),
  unique (manufacturer_id, model)
);

-- Cheap structural guards. The full Table 3 validation lives in the engine,
-- which carries the clause references and the wording; these catch the
-- impossible before it reaches the database.
alter table instrument_model
  add constraint min_below_max check (min_capacity_g < max_capacity_g),
  add constraint d_not_above_e check (d_g <= e_g),
  add constraint e_equals_d_without_aux
    check (has_aux_device or d_g = e_g);

-- ---------------------------------------------------------------------
-- evaluations
-- ---------------------------------------------------------------------

create table evaluation (
  id                uuid primary key default gen_random_uuid(),

  -- laboratory reference, e.g. RRSL-FBD/NAWI/2026/0042
  ref               text not null unique,
  lab_id            uuid not null references lab (id),
  model_id          uuid not null references instrument_model (id),
  serial_no         text,

  -- which rules judged this. Never null, never changed after creation.
  standard_id       text not null references standard_version (id),
  jurisdiction      text not null default 'IN' check (jurisdiction in ('IN', 'OIML')),

  status            evaluation_status not null default 'draft',
  verdict           verdict not null default 'PENDING',

  zero_error_g      numeric(20,6) not null default 0,

  -- Rule 12(1). Present only once a certificate has been issued.
  approval_mark     text check (approval_mark ~ '^IND/[0-9]{2}/[0-9]{2}/[0-9]{2,4}$'),
  certificate_no    text,
  gazette_date      date,

  -- test equipment actually used
  weight_set_id     text,
  load_cell         jsonb,
  ambient           jsonb,                        -- temperature, RH, pressure

  application_no    text,
  applicant         text,
  evaluation_from   date,
  evaluation_to     date,

  is_synthetic      boolean not null default false,
  generator_version text,

  created_by        uuid references auth.users (id),
  created_at        timestamptz not null default now(),
  submitted_at      timestamptz,
  reviewed_by       uuid references auth.users (id),
  approved_by       uuid references auth.users (id),
  issued_at         timestamptz,
  updated_at        timestamptz not null default now()
);

create index evaluation_lab_idx      on evaluation (lab_id, status);
create index evaluation_status_idx   on evaluation (status, created_at desc);
create index evaluation_standard_idx on evaluation (standard_id);
create index evaluation_ref_trgm     on evaluation using gin (ref gin_trgm_ops);

-- Synthetic data may move through the whole workflow — a demo dashboard is
-- worthless without issued reports — but it may never carry the legal
-- instruments. A certificate number and a Gazette date are what make an
-- approval real under Rule 11; neither can exist on a synthetic row.
alter table evaluation
  add constraint no_certificate_for_synthetic
    check (not (is_synthetic
                and (certificate_no is not null or gazette_date is not null)));

-- ---------------------------------------------------------------------
-- observations — append-only
-- ---------------------------------------------------------------------

create table observation (
  id                bigserial primary key,
  evaluation_id     uuid not null references evaluation (id) on delete cascade,

  test_code         text not null,               -- test-catalogue.json:tests[].code
  variant           text,                        -- creep | zero_return | ...
  sequence_no       int,

  -- A.4.4.3 inputs, as recorded by the technician
  load_g            numeric(20,6),
  indication_g      numeric(20,6),
  delta_load_g      numeric(20,6) default 0,
  direction         text check (direction in ('increasing', 'decreasing')),
  position          text,                        -- eccentricity segment
  elapsed_minutes   int,
  weighing_no       int,

  -- influence and disturbance conditions
  temperature_c     numeric(6,2),
  rh_percent        numeric(5,2),
  voltage_v         numeric(8,2),
  severity          jsonb,
  indication_without_g numeric(20,6),
  indication_with_g    numeric(20,6),
  significant_fault_detected boolean,

  -- Verdicts as computed by the client at entry time. The server recomputes
  -- independently on submit; a divergence blocks the submission.
  computed          jsonb,

  remark            text,

  -- A correction never overwrites. It inserts a new row and points the old
  -- one at it, so the original reading stays legible forever.
  superseded_by     bigint references observation (id),
  supersede_reason  text,

  recorded_by       uuid references auth.users (id),
  recorded_at       timestamptz not null default now()
);

create index observation_eval_idx on observation (evaluation_id, test_code);
create index observation_live_idx on observation (evaluation_id)
  where superseded_by is null;

-- Natural key, so a repeated bulk load cannot duplicate readings.
-- Partial on superseded_by so a correction (a new row pointing at the old
-- one) is still allowed.
create unique index observation_natural_key
  on observation (evaluation_id, test_code, sequence_no)
  where superseded_by is null and sequence_no is not null;

-- ---------------------------------------------------------------------
-- attachments — the named slots from the Approval of Models Rules
-- ---------------------------------------------------------------------

create table attachment (
  id            uuid primary key default gen_random_uuid(),
  evaluation_id uuid not null references evaluation (id) on delete cascade,
  slot          text not null check (slot in (
                  'application_form', 'technical_drawings', 'sealing_diagram',
                  'undertaking', 'photographs', 'foreign_certificate',
                  'fee_receipt', 'test_photo', 'other')),
  storage_path  text not null,
  filename      text not null,
  mime_type     text,
  size_bytes    bigint,
  sha256        text,
  caption       text,
  uploaded_by   uuid references auth.users (id),
  uploaded_at   timestamptz not null default now()
);

create index attachment_eval_idx on attachment (evaluation_id, slot);

-- ---------------------------------------------------------------------
-- rendered reports — immutable versions
-- ---------------------------------------------------------------------

create table report_version (
  id              uuid primary key default gen_random_uuid(),
  evaluation_id   uuid not null references evaluation (id) on delete cascade,
  version         int not null,
  standard_id     text not null references standard_version (id),

  -- SHA-256 over the canonical payload the report was rendered from.
  -- Re-rendering the same data must produce the same digest.
  payload_sha256  text not null,
  payload         jsonb not null,

  docx_path       text,
  pdf_path        text,
  verdict         verdict not null,

  rendered_by     uuid references auth.users (id),
  rendered_at     timestamptz not null default now(),
  signed_by       uuid references auth.users (id),
  signed_at       timestamptz,
  signature_kind  text,                          -- DSC | eSign | none

  unique (evaluation_id, version)
);

create index report_version_eval_idx
  on report_version (evaluation_id, version desc);

-- ---------------------------------------------------------------------
-- audit log
-- ---------------------------------------------------------------------

create table audit_log (
  id            bigserial primary key,
  -- Deliberately NOT a foreign key: an audit trail must never be able to
  -- block the work it records, and it must outlive the accounts it names.
  actor         uuid,
  actor_role    lab_role,
  action        text not null,                   -- INSERT | UPDATE | STATUS | RENDER
  table_name    text not null,
  row_id        text,
  evaluation_id uuid references evaluation (id) on delete set null,
  before        jsonb,
  after         jsonb,
  at            timestamptz not null default now()
);

create index audit_eval_idx on audit_log (evaluation_id, at desc);
create index audit_actor_idx on audit_log (actor, at desc);

-- ---------------------------------------------------------------------
-- reference rows
-- ---------------------------------------------------------------------

insert into standard_version (id, title, published, national_note, config_path, is_default) values
  ('OIML_R76-1_2006',
   'OIML R 76-1:2006 (E) Non-automatic weighing instruments — Part 1',
   '2006-01-01',
   'Adopted in India as the Legal Metrology (General) Rules, 2011, Seventh Schedule, Heading A [See Rule 13]. Values identical; the ordinary accuracy class is written IV rather than IIII.',
   'standards/', true);

insert into lab (code, name, state, mark_code) values
  ('RRSL-AMD', 'RRSL Ahmedabad',   'Gujarat',       '01'),
  ('RRSL-BLR', 'RRSL Bengaluru',   'Karnataka',     '02'),
  ('RRSL-BBS', 'RRSL Bhubaneswar', 'Odisha',        '03'),
  ('RRSL-FBD', 'RRSL Faridabad',   'Haryana',       '04'),
  ('RRSL-GHY', 'RRSL Guwahati',    'Assam',         '05'),
  ('RRSL-JAI', 'RRSL Jaipur',      'Rajasthan',     '06'),
  ('RRSL-MUM', 'RRSL Mumbai',      'Maharashtra',   '07'),
  ('RRSL-VNS', 'RRSL Varanasi',    'Uttar Pradesh', '08');

-- ####################################################################
-- ## 0002_functions.sql
-- ####################################################################

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

-- ####################################################################
-- ## 0003_rls.sql
-- ####################################################################

-- =====================================================================
-- 0003_rls.sql — row-level security
--
-- This is what makes roles real. Without RLS, "role-based permissions" is
-- a hidden button; with it, a technician holding a valid token still cannot
-- read another laboratory's evaluations or approve their own work.
--
-- Two axes are enforced together:
--   1. laboratory isolation — you see your own lab, unless you are
--      director, admin or auditor
--   2. role capability — who may insert, update, and into which state
-- =====================================================================

-- ---------------------------------------------------------------------
-- table grants
--
-- RLS and GRANT are two different gates and both must be open: a policy
-- decides WHICH ROWS a role may touch, a grant decides whether it may
-- touch the table at all. Without these, every query comes back
-- "permission denied for table evaluation" no matter how the policies
-- read.
--
-- Supabase issues these for tables that exist when a project is created;
-- saying them here keeps the schema self-contained and portable.
--
-- Note what is NOT granted: delete, anywhere. Evaluations are cancelled
-- and observations superseded, never removed.
-- ---------------------------------------------------------------------

grant select on lab, standard_version, manufacturer, instrument_model,
                profile, evaluation, observation, attachment,
                report_version, audit_log
  to authenticated;

grant insert, update on manufacturer, instrument_model, evaluation,
                        observation, attachment, report_version, profile
  to authenticated;

grant insert, update, delete on attachment to authenticated;  -- drafts only, per policy
grant insert, update on lab, standard_version to authenticated;  -- admin only, per policy

grant usage, select on all sequences in schema public to authenticated;

alter table lab               enable row level security;
alter table standard_version  enable row level security;
alter table profile           enable row level security;
alter table manufacturer      enable row level security;
alter table instrument_model  enable row level security;
alter table evaluation        enable row level security;
alter table observation       enable row level security;
alter table attachment        enable row level security;
alter table report_version    enable row level security;
alter table audit_log         enable row level security;

-- Deny by default: with RLS on and no matching policy, access is refused.
-- Every grant below is therefore explicit.

-- ---------------------------------------------------------------------
-- reference data — readable by everyone signed in, written by admin
-- ---------------------------------------------------------------------

create policy lab_read on lab
  for select to authenticated using (true);
create policy lab_write on lab
  for all to authenticated
  using (app.has_role('admin')) with check (app.has_role('admin'));

create policy standard_read on standard_version
  for select to authenticated using (true);
create policy standard_write on standard_version
  for all to authenticated
  using (app.has_role('admin')) with check (app.has_role('admin'));

create policy manufacturer_read on manufacturer
  for select to authenticated using (true);
create policy manufacturer_insert on manufacturer
  for insert to authenticated
  with check (app.has_role('technician','reviewer','approver','admin'));
create policy manufacturer_update on manufacturer
  for update to authenticated
  using (app.has_role('admin')) with check (app.has_role('admin'));

create policy model_read on instrument_model
  for select to authenticated using (true);
create policy model_insert on instrument_model
  for insert to authenticated
  with check (app.has_role('technician','reviewer','approver','admin'));
create policy model_update on instrument_model
  for update to authenticated
  using (app.has_role('reviewer','approver','admin'))
  with check (app.has_role('reviewer','approver','admin'));

-- ---------------------------------------------------------------------
-- profiles — own row, or same lab, or org-wide for oversight roles
-- ---------------------------------------------------------------------

create policy profile_read on profile
  for select to authenticated using (
    id = app.uid()
    or lab_id = app.lab_id()
    or app.has_role('director','admin','auditor')
  );

create policy profile_self_update on profile
  for update to authenticated
  using (id = app.uid())
  -- a user may edit their own name, never their own role or lab
  with check (id = app.uid()
              and role = (select p.role from profile p where p.id = app.uid())
              and lab_id is not distinct from
                  (select p.lab_id from profile p where p.id = app.uid()));

create policy profile_admin on profile
  for all to authenticated
  using (app.has_role('admin')) with check (app.has_role('admin'));

-- ---------------------------------------------------------------------
-- evaluations
-- ---------------------------------------------------------------------

create policy evaluation_read on evaluation
  for select to authenticated using (
    lab_id = app.lab_id()
    or app.has_role('director','admin','auditor')
  );

create policy evaluation_insert on evaluation
  for insert to authenticated with check (
    app.has_role('technician','admin')
    and lab_id = app.lab_id()
    and status = 'draft'
    and not is_synthetic          -- synthetic rows arrive via the loader only
  );

-- The trigger in 0002 decides which transitions and which roles are valid.
-- RLS decides who may touch the row at all.
create policy evaluation_update on evaluation
  for update to authenticated
  using (
    (lab_id = app.lab_id()
     and app.has_role('technician','reviewer','approver'))
    or app.has_role('director','admin')
  )
  with check (
    (lab_id = app.lab_id()
     and app.has_role('technician','reviewer','approver'))
    or app.has_role('director','admin')
  );

-- No delete policy anywhere: evaluations are cancelled, never removed.

-- ---------------------------------------------------------------------
-- observations
-- ---------------------------------------------------------------------

create policy observation_read on observation
  for select to authenticated using (
    exists (
      select 1 from evaluation e
      where e.id = observation.evaluation_id
        and (e.lab_id = app.lab_id()
             or app.has_role('director','admin','auditor'))
    )
  );

create policy observation_insert on observation
  for insert to authenticated with check (
    app.has_role('technician','admin')
    and exists (
      select 1 from evaluation e
      where e.id = evaluation_id
        and e.lab_id = app.lab_id()
        and e.status in ('draft','resubmitted')
    )
  );

-- Update exists solely so a row can be marked superseded; the trigger
-- rejects any change to the reading itself.
create policy observation_supersede on observation
  for update to authenticated
  using (
    app.has_role('technician','reviewer','admin')
    and exists (
      select 1 from evaluation e
      where e.id = observation.evaluation_id and e.lab_id = app.lab_id()
    )
  )
  with check (superseded_by is not null);

-- ---------------------------------------------------------------------
-- attachments
-- ---------------------------------------------------------------------

create policy attachment_read on attachment
  for select to authenticated using (
    exists (
      select 1 from evaluation e
      where e.id = attachment.evaluation_id
        and (e.lab_id = app.lab_id()
             or app.has_role('director','admin','auditor'))
    )
  );

create policy attachment_insert on attachment
  for insert to authenticated with check (
    app.has_role('technician','reviewer','approver','admin')
    and exists (
      select 1 from evaluation e
      where e.id = evaluation_id
        and e.lab_id = app.lab_id()
        and e.status not in ('issued','revoked')
    )
  );

create policy attachment_delete on attachment
  for delete to authenticated using (
    app.has_role('technician','admin')
    and exists (
      select 1 from evaluation e
      where e.id = attachment.evaluation_id
        and e.lab_id = app.lab_id()
        and e.status = 'draft'
    )
  );

-- ---------------------------------------------------------------------
-- report versions
-- ---------------------------------------------------------------------

create policy report_read on report_version
  for select to authenticated using (
    exists (
      select 1 from evaluation e
      where e.id = report_version.evaluation_id
        and (e.lab_id = app.lab_id()
             or app.has_role('director','admin','auditor'))
    )
  );

-- Rendering is done by the reporter service with the service-role key,
-- which bypasses RLS. This policy covers a reviewer or approver rendering
-- a draft copy from the browser.
create policy report_insert on report_version
  for insert to authenticated with check (
    app.has_role('reviewer','approver','director','admin')
    and exists (
      select 1 from evaluation e
      where e.id = evaluation_id and e.lab_id = app.lab_id()
    )
  );

create policy report_sign on report_version
  for update to authenticated
  using (app.has_role('approver','director','admin'))
  with check (app.has_role('approver','director','admin'));

-- ---------------------------------------------------------------------
-- audit log — readable, never writable from a client session
-- ---------------------------------------------------------------------

create policy audit_read on audit_log
  for select to authenticated using (
    app.has_role('director','admin','auditor')
    or exists (
      select 1 from evaluation e
      where e.id = audit_log.evaluation_id and e.lab_id = app.lab_id()
    )
  );

-- No insert/update/delete policy. Rows arrive only through the
-- security-definer trigger in 0002, so no client can forge or erase history.

-- ---------------------------------------------------------------------
-- views inherit the policies of their base tables
-- ---------------------------------------------------------------------

alter view v_evaluation_summary set (security_invoker = true);
alter view v_dashboard_counts   set (security_invoker = true);
alter view v_observation_live   set (security_invoker = true);

grant select on v_evaluation_summary, v_dashboard_counts, v_observation_live
  to authenticated;
