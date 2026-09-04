-- Lightweight, deterministic 2D projections produced by Model Scan.
create table if not exists public.ifc_object_plan_geometry (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  ifc_file_id uuid not null references public.ifc_files(id) on delete cascade,
  ifc_object_id uuid not null references public.ifc_objects(id) on delete cascade,
  storey_id uuid not null references public.building_storeys(id) on delete cascade,
  geometry_type text not null check (geometry_type in ('Polygon','LineString','Point')),
  geometry jsonb not null,
  centroid_x numeric,
  centroid_y numeric,
  extraction_method text not null,
  created_at timestamptz not null default now(),
  unique (ifc_file_id, ifc_object_id)
);

create index if not exists ifc_object_plan_geometry_storey_idx
  on public.ifc_object_plan_geometry(project_id, ifc_file_id, storey_id);

alter table public.ifc_object_plan_geometry enable row level security;
drop policy if exists ifc_object_plan_geometry_select on public.ifc_object_plan_geometry;
create policy ifc_object_plan_geometry_select on public.ifc_object_plan_geometry
  for select to authenticated using (public.is_project_member(project_id));
grant select on public.ifc_object_plan_geometry to authenticated;
grant all on public.ifc_object_plan_geometry to service_role;
