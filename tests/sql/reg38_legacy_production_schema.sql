-- A deliberately incomplete snapshot of the manually deployed Regulation 38
-- schema which predates the canonical Supabase migration history.  Keep this
-- fixture sparse: its purpose is to prove that 202608280002 fills schema gaps
-- without replacing relations or deleting/re-keying production records.

insert into auth.users (id, raw_user_meta_data)
values ('10000000-0000-4000-8000-000000000001', '{"full_name":"Legacy owner"}');

create table public.projects (
  id uuid primary key,
  name text not null,
  project_reference text not null,
  project_status text not null default 'DRAFT',
  country text not null default 'United Kingdom',
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.project_members (
  id uuid primary key,
  project_id uuid not null references public.projects(id),
  user_id uuid not null references auth.users(id),
  role text not null,
  created_at timestamptz not null default now()
);

create table public.buildings (
  id uuid primary key,
  project_id uuid not null references public.projects(id),
  name text not null
);

create table public.building_storeys (
  id uuid primary key,
  building_id uuid not null references public.buildings(id),
  name text not null
);

create table public.ifc_files (
  id uuid primary key,
  project_id uuid not null references public.projects(id),
  storage_path text not null,
  original_filename text not null,
  file_size bigint not null,
  uploaded_by uuid not null references auth.users(id)
);

create table public.ifc_processing_jobs (
  id uuid primary key,
  project_id uuid not null references public.projects(id),
  ifc_file_id uuid not null references public.ifc_files(id),
  status text not null
);

create table public.ifc_objects (
  id uuid primary key,
  project_id uuid not null references public.projects(id),
  ifc_file_id uuid not null references public.ifc_files(id),
  ifc_global_id text not null,
  ifc_entity text not null
);

create table public.ifc_object_properties (
  id uuid primary key,
  ifc_object_id uuid not null references public.ifc_objects(id),
  source_scope text not null,
  property_name text not null
);

-- An old policy and trigger with canonical names verify replacement is safe.
alter table public.projects enable row level security;
create policy projects_select on public.projects for select using (true);
create function public.add_project_owner() returns trigger language plpgsql as
$$ begin return new; end $$;
create trigger projects_add_owner after insert on public.projects
for each row execute function public.add_project_owner();

insert into public.projects (id,name,project_reference,created_by)
values ('20000000-0000-4000-8000-000000000001','Legacy project','LEGACY-001',
        '10000000-0000-4000-8000-000000000001');
insert into public.project_members (id,project_id,user_id,role) values
('30000000-0000-4000-8000-000000000001','20000000-0000-4000-8000-000000000001',
 '10000000-0000-4000-8000-000000000001','OWNER');
insert into public.buildings (id,project_id,name) values
('40000000-0000-4000-8000-000000000001','20000000-0000-4000-8000-000000000001','Legacy building');
insert into public.building_storeys (id,building_id,name) values
('50000000-0000-4000-8000-000000000001','40000000-0000-4000-8000-000000000001','Ground');
insert into public.ifc_files (id,project_id,storage_path,original_filename,file_size,uploaded_by) values
('60000000-0000-4000-8000-000000000001','20000000-0000-4000-8000-000000000001',
 'legacy.ifc','legacy.ifc',42,'10000000-0000-4000-8000-000000000001');
insert into public.ifc_processing_jobs (id,project_id,ifc_file_id,status) values
('70000000-0000-4000-8000-000000000001','20000000-0000-4000-8000-000000000001',
 '60000000-0000-4000-8000-000000000001','COMPLETED');
insert into public.ifc_objects (id,project_id,ifc_file_id,ifc_global_id,ifc_entity) values
('80000000-0000-4000-8000-000000000001','20000000-0000-4000-8000-000000000001',
 '60000000-0000-4000-8000-000000000001','legacy-guid','IfcWall');
insert into public.ifc_object_properties (id,ifc_object_id,source_scope,property_name) values
('90000000-0000-4000-8000-000000000001','80000000-0000-4000-8000-000000000001',
 'OCCURRENCE','FireRating');
