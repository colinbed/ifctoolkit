-- Production compatibility hotfix: the legacy projects.owner_id column remains
-- NOT NULL until a later, explicitly coordinated schema migration removes it.
alter table public.projects
  add column if not exists owner_id uuid references auth.users(id);

update public.projects
set owner_id = created_by
where owner_id is null;

alter table public.projects alter column owner_id set not null;

-- Development schemas use name while the live compatibility schema also has
-- the legacy NOT NULL title column. Keep both populated until title is retired.
alter table public.reg38_sections
  add column if not exists title text;

update public.reg38_sections
set title = name
where title is null;

alter table public.reg38_sections alter column title set not null;

create or replace function public.create_reg38_project(project_data jsonb) returns uuid
language plpgsql security definer set search_path=public as $$
declare
  pid uuid;
  actor uuid := auth.uid();
begin
  if actor is null or not public.can_create_project() then
    raise exception 'Project creation is not permitted' using errcode='42501';
  end if;

  insert into public.projects(
    name, project_reference, client_name, principal_contractor, principal_designer,
    description, building_name, building_type, project_status, planned_handover_date,
    responsible_person_name, responsible_person_email, address_line_1, address_line_2,
    town_city, county, postcode, country, owner_id, created_by
  ) values (
    project_data->>'name', project_data->>'project_reference', project_data->>'client_name',
    project_data->>'principal_contractor', project_data->>'principal_designer', project_data->>'description',
    project_data->>'building_name', project_data->>'building_type', 'DRAFT',
    nullif(project_data->>'planned_handover_date','')::date, project_data->>'responsible_person_name',
    project_data->>'responsible_person_email', project_data->>'address_line_1', project_data->>'address_line_2',
    project_data->>'town_city', project_data->>'county', project_data->>'postcode',
    coalesce(nullif(project_data->>'country',''),'United Kingdom'), actor, actor
  ) returning id into pid;

  insert into public.project_members(project_id,user_id,role)
  values(pid,actor,'OWNER')
  on conflict(project_id,user_id) do nothing;

  insert into public.reg38_sections(project_id,section_key,title,name,sort_order) values
    (pid,'PROJECT_BUILDING_INFORMATION','Project & Building Information','Project & Building Information',1),
    (pid,'FIRE_SAFETY_STRATEGY','Fire Safety Strategy','Fire Safety Strategy',2),
    (pid,'SPATIAL_OCCUPANCY','Spatial & Occupancy','Spatial & Occupancy',3),
    (pid,'ESCAPE_EVACUATION','Escape & Evacuation','Escape & Evacuation',4),
    (pid,'COMPARTMENTATION','Compartmentation','Compartmentation',5),
    (pid,'FIRE_DOORS_OPENINGS','Fire Doors & Openings','Fire Doors & Openings',6),
    (pid,'FIRE_STOPPING_PENETRATIONS','Fire Stopping / Penetrations','Fire Stopping / Penetrations',7),
    (pid,'DETECTION_ALARM','Detection & Alarm','Detection & Alarm',8),
    (pid,'EMERGENCY_LIGHTING_SIGNAGE','Emergency Lighting & Signage','Emergency Lighting & Signage',9),
    (pid,'SUPPRESSION_FIREFIGHTING','Suppression & Firefighting','Suppression & Firefighting',10),
    (pid,'SMOKE_CONTROL','Smoke Control','Smoke Control',11),
    (pid,'ELECTRICAL_CRITICAL_SYSTEMS','Electrical / Critical Systems','Electrical / Critical Systems',12),
    (pid,'FIRE_RESCUE_FACILITIES','Fire & Rescue Facilities','Fire & Rescue Facilities',13),
    (pid,'SPECIFICATIONS_OM','Specifications & O&M','Specifications & O&M',14),
    (pid,'TESTING_COMMISSIONING','Testing & Commissioning','Testing & Commissioning',15),
    (pid,'DRAWINGS_MODELS','Drawings & Models','Drawings & Models',16),
    (pid,'HANDOVER','Handover','Handover',17)
  on conflict(project_id,section_key) do nothing;

  return pid;
end $$;

revoke all on function public.create_reg38_project(jsonb) from public;
grant execute on function public.create_reg38_project(jsonb) to authenticated;
