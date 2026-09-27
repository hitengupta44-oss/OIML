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
