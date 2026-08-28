-- Regulation 38 project foundation for England and Wales.
-- Deliberately separates immutable IFC source records, reviewed working data,
-- and handover evidence. This schema supports information management; it does
-- not assert or automatically demonstrate regulatory compliance.

create extension if not exists pgcrypto;

create or replace function public.set_updated_at() returns trigger
language plpgsql set search_path = public as $$ begin new.updated_at = now(); return new; end $$;

create table public.projects (
  id uuid primary key default gen_random_uuid(),
  name text not null check (length(btrim(name)) > 0),
  project_reference text, client_name text, principal_contractor text, principal_designer text,
  description text, building_type text, project_status text not null default 'DRAFT',
  planned_handover_date date, responsible_person_name text, responsible_person_email text,
  address_line_1 text, address_line_2 text, town_city text, county text, postcode text,
  country text not null default 'United Kingdom', created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), archived_at timestamptz
);
create index projects_created_by_idx on public.projects(created_by);

create table public.project_members (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null check (role in ('OWNER','ADMIN','EDITOR','REVIEWER','VIEWER')),
  created_at timestamptz not null default now(), unique(project_id,user_id)
);
create index project_members_user_idx on public.project_members(user_id);
create index project_members_project_idx on public.project_members(project_id);

create table public.buildings (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  ifc_source_guid text, name text not null, description text, building_reference text, sort_order integer not null default 0,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index buildings_project_idx on public.buildings(project_id);

create table public.building_storeys (
  id uuid primary key default gen_random_uuid(), building_id uuid not null references public.buildings(id) on delete cascade,
  ifc_source_guid text, name text not null, long_name text, elevation numeric, sort_order integer not null default 0,
  included_in_reg38 boolean not null default true, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index building_storeys_building_idx on public.building_storeys(building_id);

create table public.ifc_files (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  storage_path text not null, original_filename text not null, file_size bigint not null check(file_size >= 0), sha256 text,
  ifc_schema text, uploaded_by uuid not null references auth.users(id), status text not null default 'UPLOADED',
  created_at timestamptz not null default now(), unique(project_id, storage_path)
);
create index ifc_files_project_idx on public.ifc_files(project_id);

create table public.ifc_processing_jobs (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  ifc_file_id uuid not null references public.ifc_files(id) on delete cascade,
  status text not null default 'QUEUED' check(status in ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED','STALE')),
  current_step text, progress_percent integer not null default 0 check(progress_percent between 0 and 100),
  error_message text, statistics jsonb not null default '{}'::jsonb, started_at timestamptz, completed_at timestamptz,
  created_at timestamptz not null default now()
);
create index ifc_processing_jobs_project_idx on public.ifc_processing_jobs(project_id);
create index ifc_processing_jobs_file_idx on public.ifc_processing_jobs(ifc_file_id);

create table public.ifc_objects (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  ifc_file_id uuid not null references public.ifc_files(id) on delete cascade,
  building_id uuid references public.buildings(id) on delete set null,
  storey_id uuid references public.building_storeys(id) on delete set null,
  ifc_global_id text not null, ifc_entity text not null, name text, long_name text, description text,
  object_type text, predefined_type text, tag text, type_global_id text,
  source_data jsonb, geometry_metadata jsonb, created_at timestamptz not null default now(),
  unique(ifc_file_id, ifc_global_id)
);
create index ifc_objects_project_idx on public.ifc_objects(project_id);
create index ifc_objects_global_id_idx on public.ifc_objects(ifc_global_id);
create index ifc_objects_entity_idx on public.ifc_objects(ifc_entity);
create index ifc_objects_building_storey_idx on public.ifc_objects(building_id,storey_id);

create table public.ifc_object_properties (
  id uuid primary key default gen_random_uuid(), ifc_object_id uuid not null references public.ifc_objects(id) on delete cascade,
  source_scope text not null check(source_scope in ('OCCURRENCE','TYPE','QUANTITY','CLASSIFICATION','ATTRIBUTE')),
  property_set text, property_name text not null, property_value_text text, property_value_number numeric,
  property_value_boolean boolean, property_unit text, raw_value jsonb, created_at timestamptz not null default now()
);
create index ifc_object_properties_object_idx on public.ifc_object_properties(ifc_object_id);
create index ifc_object_properties_name_idx on public.ifc_object_properties(property_name);
create index ifc_object_properties_set_idx on public.ifc_object_properties(property_set);

create table public.ifc_object_relationships (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  source_object_id uuid not null references public.ifc_objects(id) on delete cascade,
  target_object_id uuid not null references public.ifc_objects(id) on delete cascade,
  relationship_type text not null, source_ifc_relationship text, metadata jsonb,
  created_at timestamptz not null default now(), check(source_object_id <> target_object_id)
);
create index ifc_relationships_project_idx on public.ifc_object_relationships(project_id);
create index ifc_relationships_source_idx on public.ifc_object_relationships(source_object_id);
create index ifc_relationships_target_idx on public.ifc_object_relationships(target_object_id);

-- Reviewed project data: these rows may reference, but never overwrite, source IFC rows.
create table public.project_spaces (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  building_id uuid not null references public.buildings(id) on delete cascade,
  storey_id uuid not null references public.building_storeys(id) on delete cascade,
  source_ifc_object_id uuid references public.ifc_objects(id) on delete set null, ifc_global_id text,
  source_kind text not null check(source_kind in ('IFC_SPACE','MANUAL')),
  space_number text, name text not null, long_name text, description text, net_area numeric, gross_area numeric,
  height numeric, volume numeric, occupancy_type text, occupancy_capacity integer check(occupancy_capacity is null or occupancy_capacity >= 0),
  high_risk boolean not null default false, included_in_reg38 boolean not null default true,
  centroid_x numeric, centroid_y numeric, centroid_z numeric, source_geometry jsonb, working_geometry jsonb,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index project_spaces_project_idx on public.project_spaces(project_id);
create index project_spaces_building_storey_idx on public.project_spaces(building_id,storey_id);
create index project_spaces_global_id_idx on public.project_spaces(ifc_global_id);

create table public.project_zones (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  building_id uuid references public.buildings(id) on delete cascade, storey_id uuid references public.building_storeys(id) on delete cascade,
  source_ifc_object_id uuid references public.ifc_objects(id) on delete set null, ifc_global_id text,
  source_kind text not null check(source_kind in ('IFC_ZONE','IFC_SPATIAL_ZONE','MANUAL')),
  name text not null, description text,
  zone_type text not null check(zone_type in ('FIRE_COMPARTMENT','SMOKE_ZONE','ALARM_ZONE','SPRINKLER_ZONE','EVACUATION_ZONE','OCCUPANCY_ZONE','REFUGE','HIGH_RISK','USER_DEFINED')),
  required_fire_rating text, source_geometry jsonb, working_geometry jsonb,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index project_zones_project_idx on public.project_zones(project_id);
create index project_zones_building_storey_idx on public.project_zones(building_id,storey_id);
create index project_zones_global_id_idx on public.project_zones(ifc_global_id);

create table public.project_zone_members (
  id uuid primary key default gen_random_uuid(), zone_id uuid not null references public.project_zones(id) on delete cascade,
  space_id uuid not null references public.project_spaces(id) on delete cascade, source text not null,
  created_at timestamptz not null default now(), unique(zone_id,space_id)
);
create index project_zone_members_zone_idx on public.project_zone_members(zone_id);
create index project_zone_members_space_idx on public.project_zone_members(space_id);

create table public.project_grids (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  building_id uuid references public.buildings(id) on delete cascade,
  source_ifc_object_id uuid references public.ifc_objects(id) on delete set null, ifc_global_id text, name text,
  created_at timestamptz not null default now()
);
create index project_grids_project_idx on public.project_grids(project_id);

create table public.project_grid_axes (
  id uuid primary key default gen_random_uuid(), grid_id uuid not null references public.project_grids(id) on delete cascade,
  axis_tag text not null, axis_type text not null, same_sense boolean, geometry jsonb not null,
  created_at timestamptz not null default now()
);
create index project_grid_axes_grid_idx on public.project_grid_axes(grid_id);

create table public.fire_requirements (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  ifc_object_id uuid references public.ifc_objects(id) on delete set null, object_type text not null,
  requirement_type text not null check(requirement_type in ('FIRE_RESISTANCE','FIRE_DOOR_RATING','SMOKE_CONTROL','SELF_CLOSING','FIRE_EXIT','COMPARTMENT_BOUNDARY')),
  required_value_text text, required_minutes integer check(required_minutes is null or required_minutes >= 0),
  installed_value_text text, verified_value_text text,
  source_type text not null check(source_type in ('STANDARD_IFC_PROPERTY','KNOWN_CUSTOM_PROPERTY','FUZZY_PROPERTY_MATCH','MANUAL')),
  source_property_set text, source_property_name text, source_property_value text,
  confidence text not null check(confidence in ('HIGH','MEDIUM','LOW')),
  review_status text not null default 'UNREVIEWED' check(review_status in ('UNREVIEWED','ACCEPTED','REJECTED','CONFLICT','NEEDS_REVIEW')),
  reviewed_by uuid references auth.users(id), reviewed_at timestamptz, notes text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index fire_requirements_project_idx on public.fire_requirements(project_id);
create index fire_requirements_object_idx on public.fire_requirements(ifc_object_id);
create index fire_requirements_review_idx on public.fire_requirements(project_id,review_status);

create table public.fire_object_reviews (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  ifc_object_id uuid not null references public.ifc_objects(id) on delete cascade, classification text,
  compartment_boundary boolean, admin_notes text, reviewed_by uuid not null references auth.users(id), reviewed_at timestamptz not null default now(),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(project_id,ifc_object_id)
);
create index fire_object_reviews_project_idx on public.fire_object_reviews(project_id);

create table public.project_plans (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  building_id uuid not null references public.buildings(id) on delete cascade,
  storey_id uuid references public.building_storeys(id) on delete set null,
  source_ifc_file_id uuid references public.ifc_files(id) on delete set null,
  name text not null, plan_type text not null, revision text, storage_path text,
  width numeric, height numeric, view_box text, status text not null default 'DRAFT', is_reg38_plan boolean not null default true,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index project_plans_project_storey_idx on public.project_plans(project_id,storey_id);

create table public.plan_objects (
  id uuid primary key default gen_random_uuid(), plan_id uuid not null references public.project_plans(id) on delete cascade,
  ifc_object_id uuid references public.ifc_objects(id) on delete set null,
  space_id uuid references public.project_spaces(id) on delete set null, zone_id uuid references public.project_zones(id) on delete set null,
  svg_element_id text not null, object_category text not null, geometry jsonb, metadata jsonb,
  created_at timestamptz not null default now(), unique(plan_id,svg_element_id)
);
create index plan_objects_plan_idx on public.plan_objects(plan_id);

-- Regulation 38 workspace uses the main project as its aggregate root; no duplicate reg38_projects table.
create table public.reg38_sections (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  section_key text not null, name text not null, sort_order integer not null, enabled boolean not null default true,
  status text not null default 'NOT_STARTED', created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(project_id,section_key), unique(project_id,sort_order)
);
create index reg38_sections_project_idx on public.reg38_sections(project_id);

create table public.reg38_requirements (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  section_id uuid not null references public.reg38_sections(id) on delete cascade,
  requirement_key text not null, title text not null, description text, expected_source text,
  applicability_status text not null default 'NOT_REVIEWED', completion_status text not null default 'NOT_STARTED', notes text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(), unique(project_id,requirement_key)
);
create index reg38_requirements_project_section_idx on public.reg38_requirements(project_id,section_id);

-- Evidence is separate from both source IFC data and reviewed working records.
create table public.reg38_evidence (
  id uuid primary key default gen_random_uuid(), project_id uuid not null references public.projects(id) on delete cascade,
  section_id uuid references public.reg38_sections(id) on delete set null,
  requirement_id uuid references public.reg38_requirements(id) on delete set null,
  ifc_object_id uuid references public.ifc_objects(id) on delete set null,
  space_id uuid references public.project_spaces(id) on delete set null, zone_id uuid references public.project_zones(id) on delete set null,
  evidence_type text not null, title text not null, description text, storage_path text, external_reference text,
  revision text, status text not null default 'DRAFT', uploaded_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index reg38_evidence_project_idx on public.reg38_evidence(project_id);
create index reg38_evidence_requirement_idx on public.reg38_evidence(requirement_id);

-- Membership helpers are SECURITY DEFINER to avoid recursive project_members RLS.
create or replace function public.is_platform_admin() returns boolean language sql stable security definer
set search_path = public as $$ select exists(select 1 from public.profiles where id=(select auth.uid()) and account_level='admin') $$;
create or replace function public.is_project_member(target_project uuid) returns boolean language sql stable security definer
set search_path = public as $$ select public.is_platform_admin() or exists(select 1 from public.project_members where project_id=target_project and user_id=(select auth.uid())) $$;
create or replace function public.can_edit_project(target_project uuid) returns boolean language sql stable security definer
set search_path = public as $$ select public.is_platform_admin() or exists(select 1 from public.project_members where project_id=target_project and user_id=(select auth.uid()) and role in ('OWNER','ADMIN','EDITOR')) $$;
create or replace function public.can_manage_project(target_project uuid) returns boolean language sql stable security definer
set search_path = public as $$ select public.is_platform_admin() or exists(select 1 from public.project_members where project_id=target_project and user_id=(select auth.uid()) and role in ('OWNER','ADMIN')) $$;
create or replace function public.can_create_project() returns boolean language sql stable security definer
set search_path = public as $$ select exists(select 1 from public.profiles where id=(select auth.uid()) and (account_level in ('premium','admin') or (subscription_status='trial' and trial_ends_at > now()))) $$;

create or replace function public.add_project_owner() returns trigger language plpgsql security definer set search_path=public as $$
begin insert into public.project_members(project_id,user_id,role) values(new.id,new.created_by,'OWNER'); return new; end $$;
create trigger projects_add_owner after insert on public.projects for each row execute function public.add_project_owner();

create or replace function public.protect_project_membership() returns trigger language plpgsql security definer set search_path=public as $$
begin
  if tg_op='DELETE' then
    if not public.is_platform_admin() and old.user_id=(select auth.uid()) then
      raise exception 'Members cannot change or remove their own project role';
    end if;
    return old;
  end if;
  if not public.is_platform_admin() and old.user_id=(select auth.uid()) and new.role is distinct from old.role then
    raise exception 'Members cannot change or remove their own project role';
  end if;
  return new;
end $$;
create trigger project_members_prevent_self_escalation before update or delete on public.project_members
for each row execute function public.protect_project_membership();

-- Keep timestamps consistent across editable records.
do $$ declare t text; begin foreach t in array array['projects','buildings','building_storeys','project_spaces','project_zones','fire_requirements','fire_object_reviews','project_plans','reg38_sections','reg38_requirements','reg38_evidence'] loop
  execute format('create trigger %I_set_updated_at before update on public.%I for each row execute function public.set_updated_at()',t,t);
end loop; end $$;

-- RLS: every project record is membership-scoped. Source records are insertable by
-- editors but deliberately receive no UPDATE policy, preserving IFC provenance.
do $$ declare t text; begin foreach t in array array['projects','project_members','buildings','building_storeys','ifc_files','ifc_processing_jobs','ifc_objects','ifc_object_properties','ifc_object_relationships','project_spaces','project_zones','project_zone_members','project_grids','project_grid_axes','fire_requirements','fire_object_reviews','project_plans','plan_objects','reg38_sections','reg38_requirements','reg38_evidence'] loop
  execute format('alter table public.%I enable row level security',t);
end loop; end $$;

create policy projects_select on public.projects for select to authenticated using(public.is_project_member(id));
create policy projects_insert on public.projects for insert to authenticated with check(created_by=(select auth.uid()) and public.can_create_project());
create policy projects_update on public.projects for update to authenticated using(public.can_manage_project(id)) with check(public.can_manage_project(id));
create policy members_select on public.project_members for select to authenticated using(public.is_project_member(project_id));
create policy members_insert on public.project_members for insert to authenticated with check(public.can_manage_project(project_id));
create policy members_update on public.project_members for update to authenticated using(public.can_manage_project(project_id)) with check(public.can_manage_project(project_id));
create policy members_delete on public.project_members for delete to authenticated using(public.can_manage_project(project_id));

-- Tables carrying project_id directly.
do $$ declare t text; begin foreach t in array array['buildings','ifc_processing_jobs','ifc_objects','ifc_object_relationships','project_spaces','project_zones','project_grids','fire_requirements','project_plans','reg38_requirements'] loop
  execute format('create policy %I_select on public.%I for select to authenticated using(public.is_project_member(project_id))',t,t);
  execute format('create policy %I_insert on public.%I for insert to authenticated with check(public.can_edit_project(project_id))',t,t);
end loop; end $$;
-- Working/configuration/evidence rows are editable; raw IFC tables above are not.
do $$ declare t text; begin foreach t in array array['buildings','project_spaces','project_zones','project_grids','fire_requirements','project_plans','reg38_requirements'] loop
  execute format('create policy %I_update on public.%I for update to authenticated using(public.can_edit_project(project_id)) with check(public.can_edit_project(project_id))',t,t);
  execute format('create policy %I_delete on public.%I for delete to authenticated using(public.can_edit_project(project_id))',t,t);
end loop; end $$;

-- Actor-bearing records cannot impersonate another user.
create policy ifc_files_select on public.ifc_files for select to authenticated using(public.is_project_member(project_id));
create policy ifc_files_insert on public.ifc_files for insert to authenticated with check(public.can_edit_project(project_id) and uploaded_by=(select auth.uid()));
create policy fire_object_reviews_select on public.fire_object_reviews for select to authenticated using(public.is_project_member(project_id));
create policy fire_object_reviews_insert on public.fire_object_reviews for insert to authenticated with check(public.can_edit_project(project_id) and reviewed_by=(select auth.uid()));
create policy fire_object_reviews_update on public.fire_object_reviews for update to authenticated using(public.can_edit_project(project_id)) with check(public.can_edit_project(project_id));
create policy fire_object_reviews_delete on public.fire_object_reviews for delete to authenticated using(public.can_edit_project(project_id));
create policy reg38_evidence_select on public.reg38_evidence for select to authenticated using(public.is_project_member(project_id));
create policy reg38_evidence_insert on public.reg38_evidence for insert to authenticated with check(public.can_edit_project(project_id) and uploaded_by=(select auth.uid()));
create policy reg38_evidence_update on public.reg38_evidence for update to authenticated using(public.can_edit_project(project_id)) with check(public.can_edit_project(project_id));
create policy reg38_evidence_delete on public.reg38_evidence for delete to authenticated using(public.can_edit_project(project_id));

-- Section structure is project configuration and therefore admin-managed.
create policy reg38_sections_select on public.reg38_sections for select to authenticated using(public.is_project_member(project_id));
create policy reg38_sections_insert on public.reg38_sections for insert to authenticated with check(public.can_manage_project(project_id));
create policy reg38_sections_update on public.reg38_sections for update to authenticated using(public.can_manage_project(project_id)) with check(public.can_manage_project(project_id));
create policy reg38_sections_delete on public.reg38_sections for delete to authenticated using(public.can_manage_project(project_id));

-- Child-table policies resolve ownership through their parent.
create policy storeys_select on public.building_storeys for select to authenticated using(exists(select 1 from public.buildings b where b.id=building_id and public.is_project_member(b.project_id)));
create policy storeys_insert on public.building_storeys for insert to authenticated with check(exists(select 1 from public.buildings b where b.id=building_id and public.can_edit_project(b.project_id)));
create policy storeys_update on public.building_storeys for update to authenticated using(exists(select 1 from public.buildings b where b.id=building_id and public.can_edit_project(b.project_id)));
create policy storeys_delete on public.building_storeys for delete to authenticated using(exists(select 1 from public.buildings b where b.id=building_id and public.can_edit_project(b.project_id)));
create policy properties_select on public.ifc_object_properties for select to authenticated using(exists(select 1 from public.ifc_objects o where o.id=ifc_object_id and public.is_project_member(o.project_id)));
create policy properties_insert on public.ifc_object_properties for insert to authenticated with check(exists(select 1 from public.ifc_objects o where o.id=ifc_object_id and public.can_edit_project(o.project_id)));
create policy zone_members_select on public.project_zone_members for select to authenticated using(exists(select 1 from public.project_zones z where z.id=zone_id and public.is_project_member(z.project_id)));
create policy zone_members_insert on public.project_zone_members for insert to authenticated with check(exists(select 1 from public.project_zones z where z.id=zone_id and public.can_edit_project(z.project_id)));
create policy zone_members_delete on public.project_zone_members for delete to authenticated using(exists(select 1 from public.project_zones z where z.id=zone_id and public.can_edit_project(z.project_id)));
create policy grid_axes_select on public.project_grid_axes for select to authenticated using(exists(select 1 from public.project_grids g where g.id=grid_id and public.is_project_member(g.project_id)));
create policy grid_axes_insert on public.project_grid_axes for insert to authenticated with check(exists(select 1 from public.project_grids g where g.id=grid_id and public.can_edit_project(g.project_id)));
create policy grid_axes_update on public.project_grid_axes for update to authenticated using(exists(select 1 from public.project_grids g where g.id=grid_id and public.can_edit_project(g.project_id)));
create policy grid_axes_delete on public.project_grid_axes for delete to authenticated using(exists(select 1 from public.project_grids g where g.id=grid_id and public.can_edit_project(g.project_id)));
create policy plan_objects_select on public.plan_objects for select to authenticated using(exists(select 1 from public.project_plans p where p.id=plan_id and public.is_project_member(p.project_id)));
create policy plan_objects_insert on public.plan_objects for insert to authenticated with check(exists(select 1 from public.project_plans p where p.id=plan_id and public.can_edit_project(p.project_id)));
create policy plan_objects_update on public.plan_objects for update to authenticated using(exists(select 1 from public.project_plans p where p.id=plan_id and public.can_edit_project(p.project_id)));
create policy plan_objects_delete on public.plan_objects for delete to authenticated using(exists(select 1 from public.project_plans p where p.id=plan_id and public.can_edit_project(p.project_id)));

-- Atomic project initialisation and canonical Regulation 38 sections.
create or replace function public.create_reg38_project(project_data jsonb) returns uuid
language plpgsql security invoker set search_path=public as $$ declare pid uuid; begin
  insert into public.projects(name,project_reference,client_name,principal_contractor,principal_designer,description,building_type,project_status,planned_handover_date,responsible_person_name,responsible_person_email,address_line_1,address_line_2,town_city,county,postcode,country,created_by)
  values(project_data->>'name',project_data->>'project_reference',project_data->>'client_name',project_data->>'principal_contractor',project_data->>'principal_designer',project_data->>'description',project_data->>'building_type',coalesce(project_data->>'project_status','DRAFT'),nullif(project_data->>'planned_handover_date','')::date,project_data->>'responsible_person_name',project_data->>'responsible_person_email',project_data->>'address_line_1',project_data->>'address_line_2',project_data->>'town_city',project_data->>'county',project_data->>'postcode',coalesce(project_data->>'country','United Kingdom'),(select auth.uid())) returning id into pid;
  insert into public.reg38_sections(project_id,section_key,name,sort_order) values
   (pid,'PROJECT_BUILDING_INFORMATION','Project & Building Information',1),(pid,'FIRE_SAFETY_STRATEGY','Fire Safety Strategy',2),
   (pid,'SPATIAL_OCCUPANCY','Spatial & Occupancy',3),(pid,'ESCAPE_EVACUATION','Escape & Evacuation',4),
   (pid,'COMPARTMENTATION','Compartmentation',5),(pid,'FIRE_DOORS_OPENINGS','Fire Doors & Openings',6),
   (pid,'FIRE_STOPPING_PENETRATIONS','Fire Stopping / Penetrations',7),(pid,'DETECTION_ALARM','Detection & Alarm',8),
   (pid,'EMERGENCY_LIGHTING_SIGNAGE','Emergency Lighting & Signage',9),(pid,'SUPPRESSION_FIREFIGHTING','Suppression & Firefighting',10),
   (pid,'SMOKE_CONTROL','Smoke Control',11),(pid,'ELECTRICAL_CRITICAL_SYSTEMS','Electrical / Critical Systems',12),
   (pid,'FIRE_RESCUE_FACILITIES','Fire & Rescue Facilities',13),(pid,'SPECIFICATIONS_OM','Specifications & O&M',14),
   (pid,'TESTING_COMMISSIONING','Testing & Commissioning',15),(pid,'DRAWINGS_MODELS','Drawings & Models',16),(pid,'HANDOVER','Handover',17);
  return pid;
end $$;
grant execute on function public.create_reg38_project(jsonb) to authenticated;

-- Object storage convention: first folder is the project UUID. Policies require
-- membership and prevent clients from assigning evidence to another project.
insert into storage.buckets(id,name,public) values('reg38-evidence','reg38-evidence',false) on conflict(id) do nothing;
create or replace function public.storage_project_id(object_name text) returns uuid language plpgsql immutable
set search_path=public as $$ declare segment text := split_part(object_name,'/',1); begin
  if segment ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' then return segment::uuid; end if;
  return null;
end $$;
create policy reg38_storage_select on storage.objects for select to authenticated
using(bucket_id='reg38-evidence' and public.is_project_member(public.storage_project_id(name)));
create policy reg38_storage_insert on storage.objects for insert to authenticated
with check(bucket_id='reg38-evidence' and public.can_edit_project(public.storage_project_id(name)));
create policy reg38_storage_update on storage.objects for update to authenticated
using(bucket_id='reg38-evidence' and public.can_edit_project(public.storage_project_id(name)));
create policy reg38_storage_delete on storage.objects for delete to authenticated
using(bucket_id='reg38-evidence' and public.can_edit_project(public.storage_project_id(name)));

create or replace function public.protect_reg38_actor_fields() returns trigger language plpgsql security definer set search_path=public as $$
begin
  if public.is_platform_admin() then return new; end if;
  if tg_table_name in ('ifc_files','reg38_evidence') and
     ((tg_op='INSERT' and new.uploaded_by<>(select auth.uid())) or (tg_op='UPDATE' and new.uploaded_by is distinct from old.uploaded_by)) then
    raise exception 'uploaded_by must be the current user and cannot be changed';
  end if;
  if tg_table_name='fire_object_reviews' and new.reviewed_by<>(select auth.uid()) then
    raise exception 'reviewed_by must be the current user';
  end if;
  if tg_table_name='fire_requirements' and new.reviewed_by is not null and
     ((tg_op='INSERT' and new.reviewed_by<>(select auth.uid())) or
      (tg_op='UPDATE' and new.reviewed_by is distinct from old.reviewed_by and new.reviewed_by<>(select auth.uid()))) then
    raise exception 'reviewed_by must be the current user';
  end if;
  return new;
end $$;
create trigger ifc_files_protect_actor before insert on public.ifc_files for each row execute function public.protect_reg38_actor_fields();
create trigger evidence_protect_actor before insert or update on public.reg38_evidence for each row execute function public.protect_reg38_actor_fields();
create trigger object_reviews_protect_actor before insert or update on public.fire_object_reviews for each row execute function public.protect_reg38_actor_fields();
create trigger fire_requirements_protect_actor before insert or update on public.fire_requirements for each row execute function public.protect_reg38_actor_fields();

-- Reject cross-project references even when a caller knows another row UUID.
create or replace function public.enforce_reg38_project_consistency() returns trigger
language plpgsql set search_path=public as $$ declare parent_project uuid; parent_building uuid; begin
  case tg_table_name
    when 'ifc_processing_jobs' then select project_id into parent_project from public.ifc_files where id=new.ifc_file_id;
    when 'ifc_objects' then
      select project_id into parent_project from public.ifc_files where id=new.ifc_file_id;
      if parent_project<>new.project_id then raise exception 'IFC file belongs to another project'; end if;
      if new.building_id is not null and not exists(select 1 from public.buildings where id=new.building_id and project_id=new.project_id) then raise exception 'Building belongs to another project'; end if;
      if new.storey_id is not null and not exists(select 1 from public.building_storeys s join public.buildings b on b.id=s.building_id where s.id=new.storey_id and b.project_id=new.project_id and (new.building_id is null or b.id=new.building_id)) then raise exception 'Storey belongs to another building or project'; end if;
      return new;
    when 'ifc_object_relationships' then
      if not exists(select 1 from public.ifc_objects where id=new.source_object_id and project_id=new.project_id) or not exists(select 1 from public.ifc_objects where id=new.target_object_id and project_id=new.project_id) then raise exception 'Relationship objects belong to another project'; end if; return new;
    when 'project_spaces' then
      if not exists(select 1 from public.building_storeys s join public.buildings b on b.id=s.building_id where s.id=new.storey_id and b.id=new.building_id and b.project_id=new.project_id) then raise exception 'Space storey/building/project mismatch'; end if;
      if new.source_ifc_object_id is not null and not exists(select 1 from public.ifc_objects where id=new.source_ifc_object_id and project_id=new.project_id) then raise exception 'Space source belongs to another project'; end if; return new;
    when 'project_zones' then
      if new.building_id is not null and not exists(select 1 from public.buildings where id=new.building_id and project_id=new.project_id) then raise exception 'Zone building belongs to another project'; end if;
      if new.storey_id is not null and not exists(select 1 from public.building_storeys s join public.buildings b on b.id=s.building_id where s.id=new.storey_id and b.project_id=new.project_id and (new.building_id is null or b.id=new.building_id)) then raise exception 'Zone storey belongs to another project'; end if;
      if new.source_ifc_object_id is not null and not exists(select 1 from public.ifc_objects where id=new.source_ifc_object_id and project_id=new.project_id) then raise exception 'Zone source belongs to another project'; end if; return new;
    when 'project_zone_members' then
      select z.project_id into parent_project from public.project_zones z where z.id=new.zone_id;
      if not exists(select 1 from public.project_spaces where id=new.space_id and project_id=parent_project) then raise exception 'Zone and space belong to different projects'; end if; return new;
    when 'project_grids' then
      if new.building_id is not null and not exists(select 1 from public.buildings where id=new.building_id and project_id=new.project_id) then raise exception 'Grid building belongs to another project'; end if;
      if new.source_ifc_object_id is not null and not exists(select 1 from public.ifc_objects where id=new.source_ifc_object_id and project_id=new.project_id) then raise exception 'Grid source belongs to another project'; end if; return new;
    when 'fire_requirements' then if new.ifc_object_id is null then return new; else select project_id into parent_project from public.ifc_objects where id=new.ifc_object_id; end if;
    when 'fire_object_reviews' then select project_id into parent_project from public.ifc_objects where id=new.ifc_object_id;
    when 'project_plans' then
      if not exists(select 1 from public.buildings where id=new.building_id and project_id=new.project_id) then raise exception 'Plan building belongs to another project'; end if;
      if new.storey_id is not null and not exists(select 1 from public.building_storeys where id=new.storey_id and building_id=new.building_id) then raise exception 'Plan storey belongs to another building'; end if;
      if new.source_ifc_file_id is not null and not exists(select 1 from public.ifc_files where id=new.source_ifc_file_id and project_id=new.project_id) then raise exception 'Plan IFC file belongs to another project'; end if; return new;
    when 'plan_objects' then
      select project_id into parent_project from public.project_plans where id=new.plan_id;
      if new.ifc_object_id is not null and not exists(select 1 from public.ifc_objects where id=new.ifc_object_id and project_id=parent_project) then raise exception 'Plan object belongs to another project'; end if;
      if new.space_id is not null and not exists(select 1 from public.project_spaces where id=new.space_id and project_id=parent_project) then raise exception 'Plan space belongs to another project'; end if;
      if new.zone_id is not null and not exists(select 1 from public.project_zones where id=new.zone_id and project_id=parent_project) then raise exception 'Plan zone belongs to another project'; end if; return new;
    when 'reg38_requirements' then select project_id into parent_project from public.reg38_sections where id=new.section_id;
    when 'reg38_evidence' then
      if new.section_id is not null and not exists(select 1 from public.reg38_sections where id=new.section_id and project_id=new.project_id) then raise exception 'Evidence section belongs to another project'; end if;
      if new.requirement_id is not null and not exists(select 1 from public.reg38_requirements where id=new.requirement_id and project_id=new.project_id) then raise exception 'Evidence requirement belongs to another project'; end if;
      if new.ifc_object_id is not null and not exists(select 1 from public.ifc_objects where id=new.ifc_object_id and project_id=new.project_id) then raise exception 'Evidence IFC object belongs to another project'; end if;
      if new.space_id is not null and not exists(select 1 from public.project_spaces where id=new.space_id and project_id=new.project_id) then raise exception 'Evidence space belongs to another project'; end if;
      if new.zone_id is not null and not exists(select 1 from public.project_zones where id=new.zone_id and project_id=new.project_id) then raise exception 'Evidence zone belongs to another project'; end if; return new;
  end case;
  if parent_project is distinct from new.project_id then raise exception '% belongs to another project', tg_table_name; end if;
  return new;
end $$;
do $$ declare t text; begin foreach t in array array['ifc_processing_jobs','ifc_objects','ifc_object_relationships','project_spaces','project_zones','project_zone_members','project_grids','fire_requirements','fire_object_reviews','project_plans','plan_objects','reg38_requirements','reg38_evidence'] loop
 execute format('create trigger %I_project_consistency before insert or update on public.%I for each row execute function public.enforce_reg38_project_consistency()',t,t);
end loop; end $$;

revoke all on function public.is_platform_admin() from public;
revoke all on function public.is_project_member(uuid) from public;
revoke all on function public.can_edit_project(uuid) from public;
revoke all on function public.can_manage_project(uuid) from public;
revoke all on function public.can_create_project() from public;
grant execute on function public.is_platform_admin(), public.is_project_member(uuid), public.can_edit_project(uuid), public.can_manage_project(uuid), public.can_create_project() to authenticated;
