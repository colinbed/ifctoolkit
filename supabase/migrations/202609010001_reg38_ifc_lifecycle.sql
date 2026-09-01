-- Safe IFC lifecycle, explicit no-spaces acknowledgement, and draft deletion.
-- Storage objects are deliberately removed by the application because database
-- cascades cannot remove Supabase Storage objects.
alter table public.projects
  add column if not exists spatial_ifc_unavailable boolean not null default false,
  add column if not exists spatial_ifc_acknowledged_at timestamptz,
  add column if not exists spatial_ifc_acknowledged_by uuid references auth.users(id) on delete set null;

create table if not exists public.reg38_project_audit_events (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  actor_id uuid references auth.users(id) on delete set null,
  event_type text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
alter table public.reg38_project_audit_events enable row level security;
create policy reg38_audit_select on public.reg38_project_audit_events for select to authenticated
  using (public.is_project_member(project_id));

create or replace function public.acknowledge_reg38_missing_spatial_data(target_project uuid, target_user uuid)
returns void language plpgsql security definer set search_path=public as $$
begin
  if not public.can_edit_project(target_project) or auth.uid() <> target_user then
    raise exception 'Not authorised';
  end if;
  if not exists (
    select 1 from public.ifc_processing_jobs
    where project_id=target_project and status='COMPLETED'
      and coalesce((statistics->>'spaces')::integer,0)=0
  ) then raise exception 'A completed zero-space scan is required'; end if;
  update public.projects set spatial_ifc_unavailable=true,
    spatial_ifc_acknowledged_at=now(), spatial_ifc_acknowledged_by=target_user
  where id=target_project;
  insert into public.reg38_project_audit_events(project_id,actor_id,event_type,metadata)
  values(target_project,target_user,'MISSING_SPATIAL_IFC_ACKNOWLEDGED',jsonb_build_object('spaces',0));
end $$;
grant execute on function public.acknowledge_reg38_missing_spatial_data(uuid,uuid) to authenticated;

create or replace function public.remove_reg38_ifc_model(target_project uuid, target_file uuid)
returns void language plpgsql security definer set search_path=public as $$
begin
  if not public.can_edit_project(target_project) then raise exception 'Not authorised'; end if;
  if not exists(select 1 from public.ifc_files where id=target_file and project_id=target_project) then
    raise exception 'IFC model not found';
  end if;
  -- Preserve manually created spatial and Regulation 38 information.
  delete from public.fire_requirements where project_id=target_project and source_type <> 'MANUAL';
  delete from public.project_zones where project_id=target_project and source_kind <> 'MANUAL';
  delete from public.project_spaces where project_id=target_project and source_kind <> 'MANUAL';
  delete from public.project_grids where project_id=target_project and source_ifc_object_id in
    (select id from public.ifc_objects where ifc_file_id=target_file);
  delete from public.ifc_files where id=target_file and project_id=target_project;
  update public.projects set spatial_ifc_unavailable=false,
    spatial_ifc_acknowledged_at=null, spatial_ifc_acknowledged_by=null where id=target_project;
  insert into public.reg38_project_audit_events(project_id,actor_id,event_type,metadata)
  values(target_project,auth.uid(),'IFC_REMOVED',jsonb_build_object('ifc_file_id',target_file));
end $$;
grant execute on function public.remove_reg38_ifc_model(uuid,uuid) to authenticated;

create or replace function public.delete_draft_reg38_project(target_project uuid)
returns void language plpgsql security definer set search_path=public as $$
begin
  if not public.can_manage_project(target_project) then raise exception 'Not authorised'; end if;
  if not exists(select 1 from public.projects where id=target_project and project_status='DRAFT') then
    raise exception 'Only draft projects may be deleted';
  end if;
  delete from public.projects where id=target_project and project_status='DRAFT';
end $$;
grant execute on function public.delete_draft_reg38_project(uuid) to authenticated;
