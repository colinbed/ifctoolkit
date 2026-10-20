-- A claim response may be lost after PostgreSQL commits. Return this worker's
-- existing lease before claiming new work, so an ambiguous timeout cannot
-- strand a RUNNING job until stale recovery.
create or replace function public.claim_reg38_ifc_job(p_worker_id text)
returns table(id uuid, project_id uuid, ifc_file_id uuid, storage_path text, claim_token uuid)
language plpgsql security definer set search_path=public as $$
begin
  return query
  select j.id,j.project_id,j.ifc_file_id,f.storage_path,j.claim_token
  from public.ifc_processing_jobs j join public.ifc_files f on f.id=j.ifc_file_id
  where j.status='RUNNING' and j.worker_id=p_worker_id and j.claim_token is not null
  order by j.started_at limit 1;
  if found then return; end if;

  return query
  with candidate as (
    select j.id from public.ifc_processing_jobs j
    where j.status='QUEUED' order by j.created_at
    for update skip locked limit 1
  )
  update public.ifc_processing_jobs j
  set status='RUNNING', current_step='CLAIMED', progress_percent=1,
      started_at=now(), completed_at=null, error_message=null,
      heartbeat_at=now(), worker_id=p_worker_id, claim_token=gen_random_uuid(),
      attempt_count=j.attempt_count + 1
  from candidate c, public.ifc_files f
  where j.id=c.id and f.id=j.ifc_file_id
  returning j.id,j.project_id,j.ifc_file_id,f.storage_path,j.claim_token;
end $$;

revoke all on function public.claim_reg38_ifc_job(text) from public,anon,authenticated;
grant execute on function public.claim_reg38_ifc_job(text) to service_role;
