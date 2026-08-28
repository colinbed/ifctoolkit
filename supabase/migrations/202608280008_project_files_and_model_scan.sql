-- Private, project-scoped object storage and production model-scan lifecycle.
-- One bucket serves every project and file category; authorization comes from
-- the project UUID in the object key, never from client metadata.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('project-files', 'project-files', false, 524288000, null)
on conflict (id) do update set public=false, file_size_limit=524288000, allowed_mime_types=null;
-- The superseded bucket was introduced by an earlier unreleased migration.
-- Remove it only when empty; never destroy an existing object during rollout.
delete from storage.buckets b where b.id='reg38-evidence'
  and not exists(select 1 from storage.objects o where o.bucket_id=b.id);

create or replace function public.storage_project_id(object_name text) returns uuid
language plpgsql immutable set search_path=public as $$
declare segment text;
begin
  if split_part(object_name,'/',1) <> 'projects' then return null; end if;
  segment := split_part(object_name,'/',2);
  if segment ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    then return segment::uuid;
  end if;
  return null;
end $$;

drop policy if exists project_files_read on storage.objects;
drop policy if exists project_files_insert on storage.objects;
drop policy if exists project_files_update on storage.objects;
drop policy if exists project_files_delete on storage.objects;
create policy project_files_read on storage.objects for select to authenticated
  using (bucket_id='project-files' and public.is_project_member(public.storage_project_id(name)));
create policy project_files_insert on storage.objects for insert to authenticated
  with check (bucket_id='project-files' and public.can_edit_project(public.storage_project_id(name)));
create policy project_files_update on storage.objects for update to authenticated
  using (bucket_id='project-files' and public.can_edit_project(public.storage_project_id(name)))
  with check (bucket_id='project-files' and public.can_edit_project(public.storage_project_id(name)));
create policy project_files_delete on storage.objects for delete to authenticated
  using (bucket_id='project-files' and public.can_edit_project(public.storage_project_id(name)));

alter table public.buildings add column if not exists source_ifc_file_id uuid references public.ifc_files(id) on delete cascade;
alter table public.building_storeys add column if not exists source_ifc_file_id uuid references public.ifc_files(id) on delete cascade;
create index if not exists buildings_source_file_idx on public.buildings(source_ifc_file_id);
create index if not exists storeys_source_file_idx on public.building_storeys(source_ifc_file_id);

create table if not exists public.model_scan_warnings (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  ifc_file_id uuid not null references public.ifc_files(id) on delete cascade,
  ifc_object_id uuid references public.ifc_objects(id) on delete cascade,
  space_id uuid references public.project_spaces(id) on delete set null,
  zone_id uuid references public.project_zones(id) on delete set null,
  warning_code text not null, category text not null default 'MODEL_DATA',
  severity text not null default 'WARNING' check(severity in ('INFO','WARNING','ERROR')),
  title text not null, description text, source_data jsonb not null default '{}'::jsonb,
  review_status text not null default 'UNREVIEWED' check(review_status in ('UNREVIEWED','ACCEPTED','DISMISSED','RESOLVED')),
  reviewed_by uuid references auth.users(id), reviewed_at timestamptz,
  created_at timestamptz not null default now(), unique(ifc_file_id,ifc_object_id,warning_code)
);
create index if not exists model_scan_warnings_project_idx on public.model_scan_warnings(project_id);
alter table public.model_scan_warnings enable row level security;
create policy model_scan_warnings_select on public.model_scan_warnings for select to authenticated
  using(public.is_project_member(project_id));
create policy model_scan_warnings_review on public.model_scan_warnings for update to authenticated
  using(public.can_manage_project(project_id)) with check(public.can_manage_project(project_id));

-- Atomic metadata/job creation after the application has checked the object.
create or replace function public.finalize_ifc_upload(
  target_project uuid, target_file uuid, target_job uuid, object_path text,
  original_name text, object_size bigint
) returns uuid language plpgsql security definer set search_path=public,storage as $$
begin
  if not public.can_edit_project(target_project) then raise exception 'Not authorised'; end if;
  if object_size <= 0 or object_size > 524288000 or lower(right(original_name,4)) <> '.ifc' then
    raise exception 'Invalid IFC file';
  end if;
  if object_path <> format('projects/%s/models/%s/original/%s',target_project,target_file,original_name)
     or not exists(select 1 from storage.objects where bucket_id='project-files' and name=object_path) then
    raise exception 'Storage object is missing or path is invalid';
  end if;
  insert into public.ifc_files(id,project_id,storage_path,original_filename,file_size,uploaded_by,status)
    values(target_file,target_project,object_path,original_name,object_size,auth.uid(),'UPLOADED');
  insert into public.ifc_processing_jobs(id,project_id,ifc_file_id,status,current_step,progress_percent)
    values(target_job,target_project,target_file,'QUEUED','QUEUED',0);
  return target_job;
end $$;
revoke all on function public.finalize_ifc_upload(uuid,uuid,uuid,text,text,bigint) from public,anon;
grant execute on function public.finalize_ifc_upload(uuid,uuid,uuid,text,text,bigint) to authenticated;

-- Retry is authorized and reuses the file while source rows are deterministically
-- replaced by the worker's UUIDs. Only one active job per file is permitted.
create unique index if not exists ifc_one_active_job_idx on public.ifc_processing_jobs(ifc_file_id)
  where status in ('QUEUED','RUNNING');
create or replace function public.retry_reg38_ifc_job(target_file uuid) returns uuid
language plpgsql security definer set search_path=public as $$
declare pid uuid; jid uuid;
begin
  select project_id into pid from public.ifc_files where id=target_file;
  if pid is null or not public.can_manage_project(pid) then raise exception 'Not authorised'; end if;
  if not exists(select 1 from public.ifc_processing_jobs where ifc_file_id=target_file and status='FAILED') then
    raise exception 'Only a failed scan can be retried';
  end if;
  insert into public.ifc_processing_jobs(project_id,ifc_file_id,status,current_step,progress_percent)
    values(pid,target_file,'QUEUED','QUEUED',0) returning id into jid;
  update public.ifc_files set status='UPLOADED' where id=target_file;
  return jid;
end $$;
revoke all on function public.retry_reg38_ifc_job(uuid) from public,anon;
grant execute on function public.retry_reg38_ifc_job(uuid) to authenticated;
