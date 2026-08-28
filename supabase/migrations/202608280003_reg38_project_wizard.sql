-- Draft wizard state and safe replacement of IFC files before processing.
alter table public.projects
  add column if not exists reg38_scope_type text not null default 'ENTIRE_BUILDING',
  add column if not exists reg38_scope_detail text;
alter table public.projects drop constraint if exists projects_reg38_scope_type_check;
alter table public.projects add constraint projects_reg38_scope_type_check check
  (reg38_scope_type in ('ENTIRE_BUILDING','EXTENSION_REFURBISHMENT','SPECIFIC_BUILDING','SPECIFIC_ZONES'));

-- A queued source may be replaced by an editor; once processing starts its provenance is immutable.
create policy ifc_jobs_delete_queued on public.ifc_processing_jobs for delete to authenticated
using (status='QUEUED' and public.can_edit_project(project_id));
create policy ifc_files_delete_unprocessed on public.ifc_files for delete to authenticated
using (public.can_edit_project(project_id) and not exists
  (select 1 from public.ifc_processing_jobs j where j.ifc_file_id=ifc_files.id));

-- Wizard model paths use projects/{project_id}/models/{file_id}/{original_filename}.
create or replace function public.storage_project_id(object_name text) returns uuid language plpgsql immutable
set search_path=public as $$
declare segment text := case when split_part(object_name,'/',1)='projects' then split_part(object_name,'/',2) else split_part(object_name,'/',1) end;
begin
  if segment ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then return segment::uuid; end if;
  return null;
end $$;
