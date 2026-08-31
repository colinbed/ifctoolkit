-- Lease-backed production execution path for the dedicated Regulation 38 worker.
alter table public.ifc_processing_jobs
  add column if not exists claim_token uuid,
  add column if not exists worker_id text,
  add column if not exists heartbeat_at timestamptz,
  add column if not exists attempt_count integer not null default 0;

create index if not exists ifc_processing_jobs_claim_idx
  on public.ifc_processing_jobs(status, created_at) where status = 'QUEUED';
create index if not exists ifc_processing_jobs_stale_idx
  on public.ifc_processing_jobs(heartbeat_at) where status = 'RUNNING';

drop function if exists public.claim_reg38_ifc_job();
create function public.claim_reg38_ifc_job(p_worker_id text)
returns table(id uuid, project_id uuid, ifc_file_id uuid, storage_path text, claim_token uuid)
language plpgsql security definer set search_path=public as $$
begin
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

-- A crashed process cannot report failure. Requeue only after its lease has not
-- been renewed for the configured interval; replacement gets a new claim token.
create or replace function public.recover_stale_reg38_ifc_jobs(p_stale_seconds integer default 3600)
returns integer language plpgsql security definer set search_path=public as $$
declare recovered integer;
begin
  if p_stale_seconds < 300 then raise exception 'stale interval must be at least 300 seconds'; end if;
  update public.ifc_processing_jobs
  set status='QUEUED', current_step='RECOVERED_AFTER_WORKER_LOSS', progress_percent=0,
      worker_id=null, claim_token=null, heartbeat_at=null, started_at=null,
      error_message='Previous worker stopped responding; job safely requeued'
  where status='RUNNING' and coalesce(heartbeat_at,started_at,created_at) < now() - make_interval(secs => p_stale_seconds);
  get diagnostics recovered = row_count;
  return recovered;
end $$;

revoke all on function public.claim_reg38_ifc_job(text) from public,anon,authenticated;
revoke all on function public.recover_stale_reg38_ifc_jobs(integer) from public,anon,authenticated;
grant execute on function public.claim_reg38_ifc_job(text) to service_role;
grant execute on function public.recover_stale_reg38_ifc_jobs(integer) to service_role;
