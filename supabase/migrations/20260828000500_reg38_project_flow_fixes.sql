-- Forward-only bridge from the live 20260825103848 Regulation 38 schema.
-- This migration is intentionally self-contained: production has not run the
-- repository's 202608280001..004 development migrations.
create extension if not exists pgcrypto;

alter table public.projects
  add column if not exists name text,
  add column if not exists project_reference text,
  add column if not exists client_name text,
  add column if not exists principal_contractor text,
  add column if not exists principal_designer text,
  add column if not exists description text,
  add column if not exists building_name text,
  add column if not exists building_type text,
  add column if not exists project_status text default 'DRAFT',
  add column if not exists planned_handover_date date,
  add column if not exists responsible_person_name text,
  add column if not exists responsible_person_email text,
  add column if not exists address_line_1 text,
  add column if not exists address_line_2 text,
  add column if not exists town_city text,
  add column if not exists county text,
  add column if not exists postcode text,
  add column if not exists country text default 'United Kingdom',
  add column if not exists created_by uuid references auth.users(id),
  add column if not exists created_at timestamptz default now(),
  add column if not exists updated_at timestamptz default now(),
  add column if not exists archived_at timestamptz;

-- Retain compatibility with the old owner_id schema and make old rows valid.
do $$
begin
  if exists (select 1 from information_schema.columns where table_schema='public'
      and table_name='projects' and column_name='owner_id') then
    execute 'update public.projects set created_by=owner_id where created_by is null';
  end if;
end $$;
update public.projects set name=coalesce(nullif(btrim(name),''),'Untitled project') where name is null or btrim(name)='';
update public.projects set project_reference='LEGACY-' || left(id::text,8) where project_reference is null or btrim(project_reference)='';
update public.projects set project_status='DRAFT' where project_status is null or project_status not in ('DRAFT','ACTIVE','ARCHIVED');
update public.projects set country='United Kingdom' where country is null;
alter table public.projects alter column name set not null;
alter table public.projects alter column project_reference set not null;
alter table public.projects alter column project_status set default 'DRAFT';
alter table public.projects alter column project_status set not null;
alter table public.projects alter column country set default 'United Kingdom';
alter table public.projects alter column country set not null;
alter table public.projects alter column created_at set default now();
alter table public.projects alter column updated_at set default now();
alter table public.projects drop constraint if exists projects_project_status_check;
alter table public.projects add constraint projects_project_status_check
  check (project_status in ('DRAFT','ACTIVE','ARCHIVED'));
create index if not exists projects_created_by_idx on public.projects(created_by);

create table if not exists public.project_members (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'VIEWER',
  created_at timestamptz not null default now(),
  unique(project_id,user_id)
);
alter table public.project_members drop constraint if exists project_members_role_check;
alter table public.project_members add constraint project_members_role_check
  check(role in ('OWNER','ADMIN','EDITOR','REVIEWER','VIEWER'));
create index if not exists project_members_user_idx on public.project_members(user_id);
create index if not exists project_members_project_idx on public.project_members(project_id);
insert into public.project_members(project_id,user_id,role)
select id,created_by,'OWNER' from public.projects where created_by is not null
on conflict(project_id,user_id) do nothing;

-- Install the canonical security fields before any authorization helper uses them.
alter table public.profiles add column if not exists security_role text not null default 'MEMBER';
alter table public.profiles add column if not exists can_create_projects boolean not null default false;
alter table public.profiles drop constraint if exists profiles_security_role_check;
alter table public.profiles add constraint profiles_security_role_check
  check(security_role in ('MEMBER','ADMIN','SUPER_ADMIN'));

create or replace function public.is_platform_admin() returns boolean
language sql stable security definer set search_path=public as $$
  select exists(select 1 from public.profiles
    where id=(select auth.uid()) and security_role='SUPER_ADMIN')
$$;
create or replace function public.can_create_project() returns boolean
language sql stable security definer set search_path=public as $$
  select exists(select 1 from public.profiles where id=(select auth.uid())
    and (security_role='SUPER_ADMIN' or (security_role='ADMIN' and can_create_projects)))
$$;
create or replace function public.is_project_member(target_project uuid) returns boolean
language sql stable security definer set search_path=public as $$
  select public.is_platform_admin() or exists(select 1 from public.project_members
    where project_id=target_project and user_id=(select auth.uid()))
$$;
create or replace function public.can_manage_project(target_project uuid) returns boolean
language sql stable security definer set search_path=public as $$
  select public.is_platform_admin() or exists(select 1 from public.project_members
    where project_id=target_project and user_id=(select auth.uid()) and role in ('OWNER','ADMIN'))
$$;
create or replace function public.can_edit_project(target_project uuid) returns boolean
language sql stable security definer set search_path=public as $$
  select public.is_platform_admin() or exists(select 1 from public.project_members
    where project_id=target_project and user_id=(select auth.uid()) and role in ('OWNER','ADMIN','EDITOR'))
$$;

create table if not exists public.reg38_project_scope (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null unique references public.projects(id) on delete cascade,
  scope_type text not null,
  scope_description text,
  building_reference text,
  area_description text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint reg38_scope_type_check check(scope_type in
    ('ENTIRE_BUILDING','EXTENSION_REFURBISHMENT','SPECIFIC_BUILDING','SPECIFIC_ZONES','OTHER'))
);

alter table public.reg38_sections
  add column if not exists section_key text,
  add column if not exists name text,
  add column if not exists sort_order integer,
  add column if not exists enabled boolean default true,
  add column if not exists applicability_status text default 'APPLICABLE',
  add column if not exists completion_status text default 'NOT_STARTED',
  add column if not exists created_at timestamptz default now(),
  add column if not exists updated_at timestamptz default now();
update public.reg38_sections set section_key=coalesce(section_key,'LEGACY_' || left(id::text,8)),
  name=coalesce(name,'Legacy section'), enabled=coalesce(enabled,true),
  applicability_status=coalesce(applicability_status,'APPLICABLE'),
  completion_status=coalesce(completion_status,'NOT_STARTED');
with numbered as (select id,row_number() over(partition by project_id order by sort_order nulls last,id) n
  from public.reg38_sections) update public.reg38_sections s set sort_order=n from numbered where s.id=numbered.id;
alter table public.reg38_sections alter column section_key set not null;
alter table public.reg38_sections alter column name set not null;
alter table public.reg38_sections alter column sort_order set not null;
alter table public.reg38_sections alter column enabled set default true;
alter table public.reg38_sections alter column enabled set not null;
alter table public.reg38_sections alter column applicability_status set default 'APPLICABLE';
alter table public.reg38_sections alter column applicability_status set not null;
alter table public.reg38_sections alter column completion_status set default 'NOT_STARTED';
alter table public.reg38_sections alter column completion_status set not null;
alter table public.reg38_sections drop constraint if exists reg38_sections_applicability_check;
alter table public.reg38_sections add constraint reg38_sections_applicability_check
  check(applicability_status in ('APPLICABLE','NOT_APPLICABLE','TO_BE_CONFIRMED'));
alter table public.reg38_sections drop constraint if exists reg38_sections_completion_check;
alter table public.reg38_sections add constraint reg38_sections_completion_check
  check(completion_status in ('NOT_STARTED','IN_PROGRESS','REVIEW_REQUIRED','COMPLETE'));
create unique index if not exists reg38_sections_project_key_uidx on public.reg38_sections(project_id,section_key);
create index if not exists reg38_sections_project_order_idx on public.reg38_sections(project_id,sort_order);

create table if not exists public.ifc_files (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  storage_path text not null, original_filename text not null, file_size bigint not null check(file_size>=0),
  sha256 text, ifc_schema text, uploaded_by uuid not null references auth.users(id),
  status text not null default 'UPLOADED', created_at timestamptz not null default now(), unique(project_id,storage_path)
);
create index if not exists ifc_files_project_idx on public.ifc_files(project_id);
create table if not exists public.ifc_processing_jobs (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  ifc_file_id uuid not null references public.ifc_files(id) on delete cascade,
  status text not null default 'QUEUED', current_step text, progress_percent integer not null default 0,
  error_message text, statistics jsonb not null default '{}'::jsonb, started_at timestamptz,
  completed_at timestamptz, created_at timestamptz not null default now(),
  constraint ifc_jobs_status_check check(status in ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED','STALE')),
  constraint ifc_jobs_progress_check check(progress_percent between 0 and 100)
);
create index if not exists ifc_processing_jobs_project_idx on public.ifc_processing_jobs(project_id);
create index if not exists ifc_processing_jobs_file_idx on public.ifc_processing_jobs(ifc_file_id);

alter table public.projects enable row level security;
alter table public.project_members enable row level security;
alter table public.reg38_project_scope enable row level security;
alter table public.reg38_sections enable row level security;
alter table public.ifc_files enable row level security;
alter table public.ifc_processing_jobs enable row level security;
drop policy if exists reg38_projects_select on public.projects;
create policy reg38_projects_select on public.projects for select to authenticated using(public.is_project_member(id));
drop policy if exists reg38_projects_update on public.projects;
create policy reg38_projects_update on public.projects for update to authenticated
  using(public.can_manage_project(id)) with check(public.can_manage_project(id));
drop policy if exists reg38_members_select on public.project_members;
create policy reg38_members_select on public.project_members for select to authenticated using(public.is_project_member(project_id));
drop policy if exists reg38_scope_select on public.reg38_project_scope;
create policy reg38_scope_select on public.reg38_project_scope for select to authenticated using(public.is_project_member(project_id));
drop policy if exists reg38_scope_insert on public.reg38_project_scope;
create policy reg38_scope_insert on public.reg38_project_scope for insert to authenticated with check(public.can_manage_project(project_id));
drop policy if exists reg38_scope_update on public.reg38_project_scope;
create policy reg38_scope_update on public.reg38_project_scope for update to authenticated using(public.can_manage_project(project_id)) with check(public.can_manage_project(project_id));
drop policy if exists reg38_sections_select on public.reg38_sections;
create policy reg38_sections_select on public.reg38_sections for select to authenticated using(public.is_project_member(project_id));
drop policy if exists reg38_sections_update on public.reg38_sections;
create policy reg38_sections_update on public.reg38_sections for update to authenticated using(public.can_manage_project(project_id)) with check(public.can_manage_project(project_id));
drop policy if exists reg38_ifc_files_select on public.ifc_files;
create policy reg38_ifc_files_select on public.ifc_files for select to authenticated using(public.is_project_member(project_id));
drop policy if exists reg38_ifc_files_insert on public.ifc_files;
create policy reg38_ifc_files_insert on public.ifc_files for insert to authenticated with check(uploaded_by=(select auth.uid()) and public.can_edit_project(project_id));
drop policy if exists reg38_ifc_files_delete on public.ifc_files;
create policy reg38_ifc_files_delete on public.ifc_files for delete to authenticated using(public.can_edit_project(project_id) and not exists(select 1 from public.ifc_processing_jobs j where j.ifc_file_id=ifc_files.id));
drop policy if exists reg38_ifc_jobs_select on public.ifc_processing_jobs;
create policy reg38_ifc_jobs_select on public.ifc_processing_jobs for select to authenticated using(public.is_project_member(project_id));
drop policy if exists reg38_ifc_jobs_insert on public.ifc_processing_jobs;
create policy reg38_ifc_jobs_insert on public.ifc_processing_jobs for insert to authenticated with check(public.can_edit_project(project_id));
drop policy if exists reg38_ifc_jobs_delete on public.ifc_processing_jobs;
create policy reg38_ifc_jobs_delete on public.ifc_processing_jobs for delete to authenticated using(status='QUEUED' and public.can_edit_project(project_id));

create or replace function public.create_reg38_project(project_data jsonb) returns uuid
language plpgsql security definer set search_path=public as $$
declare pid uuid; actor uuid := auth.uid();
begin
  if actor is null or not public.can_create_project() then
    raise exception 'Project creation is not permitted' using errcode='42501';
  end if;
  insert into public.projects(name,project_reference,client_name,principal_contractor,principal_designer,
    description,building_name,building_type,project_status,planned_handover_date,responsible_person_name,
    responsible_person_email,address_line_1,address_line_2,town_city,county,postcode,country,created_by)
  values(project_data->>'name',project_data->>'project_reference',project_data->>'client_name',
    project_data->>'principal_contractor',project_data->>'principal_designer',project_data->>'description',
    project_data->>'building_name',project_data->>'building_type','DRAFT',
    nullif(project_data->>'planned_handover_date','')::date,project_data->>'responsible_person_name',
    project_data->>'responsible_person_email',project_data->>'address_line_1',project_data->>'address_line_2',
    project_data->>'town_city',project_data->>'county',project_data->>'postcode',
    coalesce(nullif(project_data->>'country',''),'United Kingdom'),actor) returning id into pid;
  insert into public.project_members(project_id,user_id,role) values(pid,actor,'OWNER')
    on conflict(project_id,user_id) do nothing;
  insert into public.reg38_sections(project_id,section_key,name,sort_order) values
    (pid,'PROJECT_BUILDING_INFORMATION','Project & Building Information',1),(pid,'FIRE_SAFETY_STRATEGY','Fire Safety Strategy',2),(pid,'SPATIAL_OCCUPANCY','Spatial & Occupancy',3),(pid,'ESCAPE_EVACUATION','Escape & Evacuation',4),(pid,'COMPARTMENTATION','Compartmentation',5),(pid,'FIRE_DOORS_OPENINGS','Fire Doors & Openings',6),(pid,'FIRE_STOPPING_PENETRATIONS','Fire Stopping / Penetrations',7),(pid,'DETECTION_ALARM','Detection & Alarm',8),(pid,'EMERGENCY_LIGHTING_SIGNAGE','Emergency Lighting & Signage',9),(pid,'SUPPRESSION_FIREFIGHTING','Suppression & Firefighting',10),(pid,'SMOKE_CONTROL','Smoke Control',11),(pid,'ELECTRICAL_CRITICAL_SYSTEMS','Electrical / Critical Systems',12),(pid,'FIRE_RESCUE_FACILITIES','Fire & Rescue Facilities',13),(pid,'SPECIFICATIONS_OM','Specifications & O&M',14),(pid,'TESTING_COMMISSIONING','Testing & Commissioning',15),(pid,'DRAWINGS_MODELS','Drawings & Models',16),(pid,'HANDOVER','Handover',17)
    on conflict(project_id,section_key) do nothing;
  return pid;
end $$;
create or replace function public.save_reg38_scope(target_project_id uuid,scope_data jsonb) returns void
language plpgsql security invoker set search_path=public as $$
begin
  if not public.can_manage_project(target_project_id) then raise exception 'Project configuration is not permitted' using errcode='42501'; end if;
  insert into public.reg38_project_scope(project_id,scope_type,scope_description,building_reference,area_description)
  values(target_project_id,scope_data->>'scope_type',scope_data->>'scope_description',scope_data->>'building_reference',scope_data->>'area_description')
  on conflict(project_id) do update set scope_type=excluded.scope_type,scope_description=excluded.scope_description,
    building_reference=excluded.building_reference,area_description=excluded.area_description,updated_at=now();
end $$;

grant select,update on public.projects to authenticated;
grant select on public.project_members to authenticated;
grant select,insert,update on public.reg38_project_scope to authenticated;
grant select,update on public.reg38_sections to authenticated;
grant select,insert,delete on public.ifc_files,public.ifc_processing_jobs to authenticated;
revoke all on function public.is_platform_admin(),public.can_create_project(),public.is_project_member(uuid),
  public.can_manage_project(uuid),public.can_edit_project(uuid),public.create_reg38_project(jsonb),
  public.save_reg38_scope(uuid,jsonb) from public;
grant execute on function public.is_platform_admin(),public.can_create_project(),public.is_project_member(uuid),
  public.can_manage_project(uuid),public.can_edit_project(uuid),public.create_reg38_project(jsonb),
  public.save_reg38_scope(uuid,jsonb) to authenticated;
