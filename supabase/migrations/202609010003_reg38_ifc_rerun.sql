-- Queue a new scan for an unchanged, already-processed IFC. Job rows are
-- immutable history; the partial unique index continues to prevent duplicates.
create or replace function public.rerun_reg38_ifc_job(target_file uuid) returns uuid
language plpgsql security definer set search_path=public as $$
declare pid uuid; jid uuid; latest_status text;
begin
  select project_id into pid from public.ifc_files where id=target_file;
  if pid is null or not public.can_manage_project(pid) then raise exception 'Not authorised'; end if;

  select status into latest_status from public.ifc_processing_jobs
    where ifc_file_id=target_file order by created_at desc, id desc limit 1;
  if latest_status not in ('COMPLETED','SUCCEEDED') then
    raise exception 'Only a completed Model Scan can be re-run';
  end if;

  insert into public.ifc_processing_jobs(project_id,ifc_file_id,status,current_step,progress_percent)
    values(pid,target_file,'QUEUED','QUEUED',0) returning id into jid;
  -- Keep the file and storage_path unchanged. This status lets existing UI and
  -- worker lifecycle code represent the new scan without touching prior jobs.
  update public.ifc_files set status='UPLOADED' where id=target_file;
  return jid;
end $$;
revoke all on function public.rerun_reg38_ifc_job(uuid) from public,anon;
grant execute on function public.rerun_reg38_ifc_job(uuid) to authenticated;
