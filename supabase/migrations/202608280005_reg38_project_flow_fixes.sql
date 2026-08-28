-- Complete the Regulation 38 setup data model and repair atomic project creation.
alter table public.projects add column if not exists building_name text;
alter table public.projects alter column project_reference set not null;
alter table public.projects drop constraint if exists projects_project_status_check;
alter table public.projects add constraint projects_project_status_check check (project_status in ('DRAFT','ACTIVE'));

create table if not exists public.reg38_project_scope (
 id uuid primary key default gen_random_uuid(), project_id uuid not null unique references public.projects(id) on delete cascade,
 scope_type text not null, scope_description text, building_reference text, area_description text,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 constraint reg38_scope_type_check check(scope_type in ('ENTIRE_BUILDING','EXTENSION_REFURBISHMENT','SPECIFIC_BUILDING','SPECIFIC_ZONES','OTHER'))
);
alter table public.reg38_project_scope enable row level security;
create policy reg38_scope_select on public.reg38_project_scope for select to authenticated using(public.is_project_member(project_id));
create policy reg38_scope_insert on public.reg38_project_scope for insert to authenticated with check(public.can_manage_project(project_id));
create policy reg38_scope_update on public.reg38_project_scope for update to authenticated using(public.can_manage_project(project_id)) with check(public.can_manage_project(project_id));

alter table public.reg38_sections add column if not exists applicability_status text not null default 'APPLICABLE';
alter table public.reg38_sections add column if not exists completion_status text not null default 'NOT_STARTED';
alter table public.reg38_sections add constraint reg38_sections_applicability_check check(applicability_status in ('APPLICABLE','NOT_APPLICABLE','TO_BE_CONFIRMED'));
alter table public.reg38_sections add constraint reg38_sections_completion_check check(completion_status in ('NOT_STARTED','IN_PROGRESS','REVIEW_REQUIRED','COMPLETE'));

create or replace function public.create_reg38_project(project_data jsonb) returns uuid
language plpgsql security definer set search_path=public as $$ declare pid uuid; actor uuid := auth.uid(); begin
  if actor is null or not public.can_create_project() then raise exception 'Project creation is not permitted' using errcode='42501'; end if;
  insert into public.projects(name,project_reference,client_name,principal_contractor,principal_designer,description,building_name,building_type,project_status,planned_handover_date,responsible_person_name,responsible_person_email,address_line_1,address_line_2,town_city,county,postcode,country,created_by)
  values(project_data->>'name',project_data->>'project_reference',project_data->>'client_name',project_data->>'principal_contractor',project_data->>'principal_designer',project_data->>'description',project_data->>'building_name',project_data->>'building_type','DRAFT',nullif(project_data->>'planned_handover_date','')::date,project_data->>'responsible_person_name',project_data->>'responsible_person_email',project_data->>'address_line_1',project_data->>'address_line_2',project_data->>'town_city',project_data->>'county',project_data->>'postcode',coalesce(nullif(project_data->>'country',''),'United Kingdom'),actor) returning id into pid;
  insert into public.project_members(project_id,user_id,role) values(pid,actor,'OWNER');
  insert into public.reg38_sections(project_id,section_key,name,sort_order) values
  (pid,'PROJECT_BUILDING_INFORMATION','Project & Building Information',1),(pid,'FIRE_SAFETY_STRATEGY','Fire Safety Strategy',2),(pid,'SPATIAL_OCCUPANCY','Spatial & Occupancy',3),(pid,'ESCAPE_EVACUATION','Escape & Evacuation',4),(pid,'COMPARTMENTATION','Compartmentation',5),(pid,'FIRE_DOORS_OPENINGS','Fire Doors & Openings',6),(pid,'FIRE_STOPPING_PENETRATIONS','Fire Stopping / Penetrations',7),(pid,'DETECTION_ALARM','Detection & Alarm',8),(pid,'EMERGENCY_LIGHTING_SIGNAGE','Emergency Lighting & Signage',9),(pid,'SUPPRESSION_FIREFIGHTING','Suppression & Firefighting',10),(pid,'SMOKE_CONTROL','Smoke Control',11),(pid,'ELECTRICAL_CRITICAL_SYSTEMS','Electrical / Critical Systems',12),(pid,'FIRE_RESCUE_FACILITIES','Fire & Rescue Facilities',13),(pid,'SPECIFICATIONS_OM','Specifications & O&M',14),(pid,'TESTING_COMMISSIONING','Testing & Commissioning',15),(pid,'DRAWINGS_MODELS','Drawings & Models',16),(pid,'HANDOVER','Handover',17) on conflict(project_id,section_key) do nothing;
  return pid;
end $$;

create or replace function public.save_reg38_scope(target_project_id uuid, scope_data jsonb) returns void
language plpgsql security invoker set search_path=public as $$ begin
 if not public.can_manage_project(target_project_id) then raise exception 'Project configuration is not permitted' using errcode='42501'; end if;
 insert into public.reg38_project_scope(project_id,scope_type,scope_description,building_reference,area_description)
 values(target_project_id,scope_data->>'scope_type',scope_data->>'scope_description',scope_data->>'building_reference',scope_data->>'area_description')
 on conflict(project_id) do update set scope_type=excluded.scope_type,scope_description=excluded.scope_description,building_reference=excluded.building_reference,area_description=excluded.area_description,updated_at=now();
end $$;
grant execute on function public.save_reg38_scope(uuid,jsonb) to authenticated;
