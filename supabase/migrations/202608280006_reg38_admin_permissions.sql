-- Separate security roles from commercial account entitlements and expose a
-- non-mutating schema diagnostic for Regulation 38 deployments.
alter table public.profiles add column if not exists security_role text not null default 'MEMBER';
alter table public.profiles add column if not exists can_create_projects boolean not null default false;

alter table public.profiles drop constraint if exists profiles_security_role_check;
alter table public.profiles add constraint profiles_security_role_check
  check (security_role in ('MEMBER', 'ADMIN', 'SUPER_ADMIN'));

-- Preserve the existing platform administrators while removing account_level
-- from all future authorization decisions.
update public.profiles set security_role = 'SUPER_ADMIN'
where account_level = 'admin' and security_role = 'MEMBER';

create or replace function public.is_platform_admin() returns boolean
language sql stable security definer set search_path = public
as $$
  select exists (
    select 1 from public.profiles
    where id = (select auth.uid()) and security_role = 'SUPER_ADMIN'
  )
$$;

create or replace function public.can_create_project() returns boolean
language sql stable security definer set search_path = public
as $$
  select exists (
    select 1 from public.profiles
    where id = (select auth.uid())
      and (security_role = 'SUPER_ADMIN'
        or (security_role = 'ADMIN' and can_create_projects))
  )
$$;

create or replace function public.reg38_schema_health() returns jsonb
language sql stable security definer set search_path = public
as $$
  with required(table_name, column_name) as (values
    ('projects','id'), ('projects','building_name'), ('projects','project_status'),
    ('projects','archived_at'), ('project_members','project_id'), ('project_members','created_at'),
    ('reg38_sections','completion_status'), ('reg38_sections','applicability_status'),
    ('reg38_project_scope','project_id'), ('ifc_files','project_id'),
    ('ifc_processing_jobs','project_id')
  ), missing as (
    select r.table_name || '.' || r.column_name as item from required r
    where not exists (select 1 from information_schema.columns c
      where c.table_schema='public' and c.table_name=r.table_name and c.column_name=r.column_name)
  )
  select jsonb_build_object('valid', not exists(select 1 from missing),
    'missing', coalesce((select jsonb_agg(item order by item) from missing), '[]'::jsonb))
$$;

revoke all on function public.is_platform_admin() from public;
revoke all on function public.can_create_project() from public;
revoke all on function public.reg38_schema_health() from public;
grant execute on function public.is_platform_admin(), public.can_create_project(), public.reg38_schema_health() to authenticated;

-- The projects_add_owner trigger may already have inserted this membership. Keep
-- atomic creation idempotent across both migration states.
create or replace function public.create_reg38_project(project_data jsonb) returns uuid
language plpgsql security definer set search_path=public as $$ declare pid uuid; actor uuid := auth.uid(); begin
  if actor is null or not public.can_create_project() then raise exception 'Project creation is not permitted' using errcode='42501'; end if;
  insert into public.projects(name,project_reference,client_name,principal_contractor,principal_designer,description,building_name,building_type,project_status,planned_handover_date,responsible_person_name,responsible_person_email,address_line_1,address_line_2,town_city,county,postcode,country,created_by)
  values(project_data->>'name',project_data->>'project_reference',project_data->>'client_name',project_data->>'principal_contractor',project_data->>'principal_designer',project_data->>'description',project_data->>'building_name',project_data->>'building_type','DRAFT',nullif(project_data->>'planned_handover_date','')::date,project_data->>'responsible_person_name',project_data->>'responsible_person_email',project_data->>'address_line_1',project_data->>'address_line_2',project_data->>'town_city',project_data->>'county',project_data->>'postcode',coalesce(nullif(project_data->>'country',''),'United Kingdom'),actor) returning id into pid;
  insert into public.project_members(project_id,user_id,role) values(pid,actor,'OWNER') on conflict(project_id,user_id) do nothing;
  insert into public.reg38_sections(project_id,section_key,name,sort_order) values
  (pid,'PROJECT_BUILDING_INFORMATION','Project & Building Information',1),(pid,'FIRE_SAFETY_STRATEGY','Fire Safety Strategy',2),(pid,'SPATIAL_OCCUPANCY','Spatial & Occupancy',3),(pid,'ESCAPE_EVACUATION','Escape & Evacuation',4),(pid,'COMPARTMENTATION','Compartmentation',5),(pid,'FIRE_DOORS_OPENINGS','Fire Doors & Openings',6),(pid,'FIRE_STOPPING_PENETRATIONS','Fire Stopping / Penetrations',7),(pid,'DETECTION_ALARM','Detection & Alarm',8),(pid,'EMERGENCY_LIGHTING_SIGNAGE','Emergency Lighting & Signage',9),(pid,'SUPPRESSION_FIREFIGHTING','Suppression & Firefighting',10),(pid,'SMOKE_CONTROL','Smoke Control',11),(pid,'ELECTRICAL_CRITICAL_SYSTEMS','Electrical / Critical Systems',12),(pid,'FIRE_RESCUE_FACILITIES','Fire & Rescue Facilities',13),(pid,'SPECIFICATIONS_OM','Specifications & O&M',14),(pid,'TESTING_COMMISSIONING','Testing & Commissioning',15),(pid,'DRAWINGS_MODELS','Drawings & Models',16),(pid,'HANDOVER','Handover',17) on conflict(project_id,section_key) do nothing;
  return pid;
end $$;

