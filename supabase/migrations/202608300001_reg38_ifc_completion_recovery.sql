-- Idempotent Regulation 38 IFC finalisation and a deploy-time schema audit.
create or replace function public.finalize_ifc_upload(
  target_project uuid, target_file uuid, target_job uuid, object_path text,
  original_name text, object_size bigint
) returns uuid language plpgsql security definer set search_path=public,storage as $$
declare existing_job uuid;
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
    values(target_file,target_project,object_path,original_name,object_size,auth.uid(),'UPLOADED')
  on conflict (id) do update set storage_path=excluded.storage_path,
    original_filename=excluded.original_filename, file_size=excluded.file_size, status='UPLOADED'
  where ifc_files.project_id=excluded.project_id;
  if not found then raise exception 'IFC model belongs to another project'; end if;

  select id into existing_job from public.ifc_processing_jobs
    where project_id=target_project and ifc_file_id=target_file and status in ('QUEUED','RUNNING')
    order by created_at desc limit 1;
  if existing_job is null then
    insert into public.ifc_processing_jobs(id,project_id,ifc_file_id,status,current_step,progress_percent)
      values(target_job,target_project,target_file,'QUEUED','QUEUED',0)
      returning id into existing_job;
  end if;
  return existing_job;
end $$;
revoke all on function public.finalize_ifc_upload(uuid,uuid,uuid,text,text,bigint) from public,anon;
grant execute on function public.finalize_ifc_upload(uuid,uuid,uuid,text,text,bigint) to authenticated;

create or replace function public.reg38_schema_health() returns jsonb
language sql stable security definer set search_path=public as $$
  with required_relations(name) as (values
    ('projects'),('project_members'),('reg38_project_scope'),('reg38_sections'),
    ('ifc_files'),('ifc_processing_jobs')
  ), required_functions(name) as (values
    ('is_project_member'),('can_edit_project'),('create_reg38_project'),
    ('save_reg38_scope'),('finalize_ifc_upload'),('reg38_schema_health')
  ), missing as (
    select 'table:' || name item from required_relations where to_regclass('public.' || name) is null
    union all
    select 'function:' || name from required_functions
      where not exists(select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                       where n.nspname='public' and p.proname=required_functions.name)
  )
  select jsonb_build_object('valid',not exists(select 1 from missing),
    'missing',coalesce((select jsonb_agg(item order by item) from missing),'[]'::jsonb))
$$;
revoke all on function public.reg38_schema_health() from public,anon;
grant execute on function public.reg38_schema_health() to authenticated;
