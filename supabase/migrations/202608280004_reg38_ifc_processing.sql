-- Background model-scan support. Stages are stored in current_step while the
-- coarse job status remains suitable for queue claiming and lifecycle filters.
alter table public.project_zones add column source_predefined_type text;
alter table public.fire_requirements add column source_scope text
  check(source_scope in ('OCCURRENCE','TYPE','QUANTITY','CLASSIFICATION','ATTRIBUTE'));
alter table public.fire_requirements add column smoke_indication boolean not null default false;

create table public.ifc_scan_warnings (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  ifc_file_id uuid not null references public.ifc_files(id) on delete cascade,
  ifc_object_id uuid references public.ifc_objects(id) on delete cascade,
  warning_code text not null,
  message text not null,
  severity text not null default 'WARNING' check(severity in ('INFO','WARNING','ERROR')),
  created_at timestamptz not null default now(),
  unique(ifc_file_id,ifc_object_id,warning_code)
);
create index ifc_scan_warnings_project_idx on public.ifc_scan_warnings(project_id);
alter table public.ifc_scan_warnings enable row level security;
create policy ifc_scan_warnings_select on public.ifc_scan_warnings for select to authenticated
  using(public.is_project_member(project_id));

-- Called only with a service-role key by the non-interactive worker. SKIP LOCKED
-- permits multiple worker containers without processing a model twice.
create or replace function public.claim_reg38_ifc_job()
returns table(id uuid, project_id uuid, ifc_file_id uuid, storage_path text)
language plpgsql security definer set search_path=public as $$
begin
  return query
  with candidate as (
    select j.id from public.ifc_processing_jobs j where j.status='QUEUED'
    order by j.created_at for update skip locked limit 1
  )
  update public.ifc_processing_jobs j set status='RUNNING', current_step='UPLOADED',
    progress_percent=0, started_at=now()
  from candidate c, public.ifc_files f
  where j.id=c.id and f.id=j.ifc_file_id
  returning j.id,j.project_id,j.ifc_file_id,f.storage_path;
end $$;
revoke all on function public.claim_reg38_ifc_job() from public, anon, authenticated;
grant execute on function public.claim_reg38_ifc_job() to service_role;
