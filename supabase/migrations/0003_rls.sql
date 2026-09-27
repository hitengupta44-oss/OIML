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
