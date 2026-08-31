-- Production recovery for the Model Scan warning relation omitted from a deployment.
-- This migration is intentionally idempotent and contains the relation's complete
-- indexes, constraints, grants-by-policy and RLS path.
create table if not exists public.model_scan_warnings (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  ifc_file_id uuid not null references public.ifc_files(id) on delete cascade,
  ifc_object_id uuid references public.ifc_objects(id) on delete cascade,
  space_id uuid references public.project_spaces(id) on delete set null,
  zone_id uuid references public.project_zones(id) on delete set null,
  warning_code text not null,
  category text not null default 'MODEL_DATA',
  severity text not null default 'WARNING' check(severity in ('INFO','WARNING','ERROR')),
  title text not null,
  description text,
  source_data jsonb not null default '{}'::jsonb,
  review_status text not null default 'UNREVIEWED'
    check(review_status in ('UNREVIEWED','ACCEPTED','DISMISSED','RESOLVED')),
  reviewed_by uuid references auth.users(id),
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  unique(ifc_file_id,ifc_object_id,warning_code)
);

create index if not exists model_scan_warnings_project_idx
  on public.model_scan_warnings(project_id);
alter table public.model_scan_warnings enable row level security;
drop policy if exists model_scan_warnings_select on public.model_scan_warnings;
drop policy if exists model_scan_warnings_review on public.model_scan_warnings;
create policy model_scan_warnings_select on public.model_scan_warnings for select to authenticated
  using(public.is_project_member(project_id));
create policy model_scan_warnings_review on public.model_scan_warnings for update to authenticated
  using(public.can_manage_project(project_id)) with check(public.can_manage_project(project_id));

-- Audit every relation directly queried while rendering Model Scan, plus the
-- RPCs needed to upload, process and retry the model displayed by that page.
create or replace function public.reg38_schema_health() returns jsonb
language sql stable security definer set search_path=public as $$
  with required_relations(name) as (values
    ('projects'),('project_members'),('reg38_project_scope'),('reg38_sections'),
    ('ifc_files'),('ifc_processing_jobs'),('model_scan_warnings')
  ), required_functions(name) as (values
    ('is_project_member'),('can_edit_project'),('can_manage_project'),
    ('create_reg38_project'),('save_reg38_scope'),('finalize_ifc_upload'),
    ('claim_reg38_ifc_job'),('retry_reg38_ifc_job'),('reg38_schema_health')
  ), missing as (
    select 'table:' || name item from required_relations
      where to_regclass('public.' || name) is null
    union all
    select 'function:' || name from required_functions
      where not exists(select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                       where n.nspname='public' and p.proname=required_functions.name)
  )
  select jsonb_build_object(
    'valid',not exists(select 1 from missing),
    'missing',coalesce((select jsonb_agg(item order by item) from missing),'[]'::jsonb)
  )
$$;
revoke all on function public.reg38_schema_health() from public,anon;
grant execute on function public.reg38_schema_health() to authenticated;
