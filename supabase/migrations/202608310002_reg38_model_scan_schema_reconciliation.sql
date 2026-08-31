-- Reconcile the complete Model Scan persistence contract.  Every operation is
-- repeatable so this can repair drift without replacing or deleting live rows.
create table if not exists public.ifc_objects (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  ifc_file_id uuid not null references public.ifc_files(id) on delete cascade,
  building_id uuid references public.buildings(id) on delete set null,
  storey_id uuid references public.building_storeys(id) on delete set null,
  ifc_global_id text not null,
  ifc_entity text not null,
  name text,
  long_name text,
  description text,
  object_type text,
  predefined_type text,
  tag text,
  type_global_id text,
  source_data jsonb,
  geometry_metadata jsonb,
  created_at timestamptz not null default now(),
  unique(ifc_file_id, ifc_global_id)
);

create index if not exists ifc_objects_project_idx on public.ifc_objects(project_id);
create index if not exists ifc_objects_global_id_idx on public.ifc_objects(ifc_global_id);
create index if not exists ifc_objects_entity_idx on public.ifc_objects(ifc_entity);
create index if not exists ifc_objects_building_storey_idx on public.ifc_objects(building_id, storey_id);
alter table public.ifc_objects enable row level security;
drop policy if exists ifc_objects_select on public.ifc_objects;
drop policy if exists ifc_objects_insert on public.ifc_objects;
create policy ifc_objects_select on public.ifc_objects for select to authenticated
  using (public.is_project_member(project_id));
create policy ifc_objects_insert on public.ifc_objects for insert to authenticated
  with check (public.can_edit_project(project_id));
drop trigger if exists ifc_objects_project_consistency on public.ifc_objects;
create trigger ifc_objects_project_consistency before insert or update on public.ifc_objects
  for each row execute function public.enforce_reg38_project_consistency();

create table if not exists public.model_scan_warnings (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  ifc_file_id uuid not null references public.ifc_files(id) on delete cascade,
  ifc_object_id uuid references public.ifc_objects(id) on delete cascade,
  space_id uuid references public.project_spaces(id) on delete set null,
  zone_id uuid references public.project_zones(id) on delete set null,
  warning_code text not null,
  category text not null default 'MODEL_DATA',
  severity text not null default 'WARNING' check (severity in ('INFO','WARNING','ERROR')),
  title text not null,
  description text,
  source_data jsonb not null default '{}'::jsonb,
  review_status text not null default 'UNREVIEWED'
    check (review_status in ('UNREVIEWED','ACCEPTED','DISMISSED','RESOLVED')),
  reviewed_by uuid references auth.users(id),
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  unique(ifc_file_id, ifc_object_id, warning_code)
);

create index if not exists model_scan_warnings_project_idx on public.model_scan_warnings(project_id);
create index if not exists model_scan_warnings_file_idx on public.model_scan_warnings(ifc_file_id);
alter table public.model_scan_warnings enable row level security;
drop policy if exists model_scan_warnings_select on public.model_scan_warnings;
drop policy if exists model_scan_warnings_review on public.model_scan_warnings;
create policy model_scan_warnings_select on public.model_scan_warnings for select to authenticated
  using (public.is_project_member(project_id));
create policy model_scan_warnings_review on public.model_scan_warnings for update to authenticated
  using (public.can_manage_project(project_id)) with check (public.can_manage_project(project_id));

-- The original shared trigger referenced fields belonging to other tables in
-- independent IF expressions. PostgreSQL may evaluate those record accesses
-- even when the table-name predicate is false. Branch first so IFC upload rows
-- never attempt to read a non-existent reviewed_by field.
create or replace function public.protect_reg38_actor_fields() returns trigger
language plpgsql security definer set search_path=public as $$
begin
  if public.is_platform_admin() then return new; end if;
  case tg_table_name
    when 'ifc_files', 'reg38_evidence' then
      if (tg_op='INSERT' and new.uploaded_by<>(select auth.uid())) or
         (tg_op='UPDATE' and new.uploaded_by is distinct from old.uploaded_by) then
        raise exception 'uploaded_by must be the current user and cannot be changed';
      end if;
    when 'fire_object_reviews' then
      if new.reviewed_by<>(select auth.uid()) then
        raise exception 'reviewed_by must be the current user';
      end if;
    when 'fire_requirements' then
      if new.reviewed_by is not null and
         ((tg_op='INSERT' and new.reviewed_by<>(select auth.uid())) or
          (tg_op='UPDATE' and new.reviewed_by is distinct from old.reviewed_by and
           new.reviewed_by<>(select auth.uid()))) then
        raise exception 'reviewed_by must be the current user';
      end if;
  end case;
  return new;
end $$;

-- Cover both the browser reads and every parent/child relation written by the
-- Model Scan worker. Column checks catch partially-created relations as well as
-- wholly missing ones, while function checks cover the scan lifecycle RPCs.
create or replace function public.reg38_schema_health() returns jsonb
language sql stable security definer set search_path=public as $$
  with required_relations(name) as (values
    ('profiles'),('projects'),('project_members'),('reg38_project_scope'),('reg38_sections'),
    ('ifc_files'),('ifc_processing_jobs'),('buildings'),('building_storeys'),
    ('ifc_objects'),('ifc_object_properties'),('ifc_object_relationships'),
    ('project_spaces'),('project_zones'),('project_zone_members'),
    ('project_grids'),('project_grid_axes'),('fire_requirements'),('model_scan_warnings')
  ), required_columns(relation_name,column_name) as (values
    ('ifc_objects','id'),('ifc_objects','project_id'),('ifc_objects','ifc_file_id'),
    ('ifc_objects','ifc_global_id'),('ifc_objects','ifc_entity'),
    ('model_scan_warnings','id'),('model_scan_warnings','project_id'),
    ('model_scan_warnings','ifc_file_id'),('model_scan_warnings','ifc_object_id'),
    ('model_scan_warnings','warning_code'),('model_scan_warnings','review_status')
  ), required_functions(name) as (values
    ('is_project_member'),('can_edit_project'),('can_manage_project'),
    ('create_reg38_project'),('save_reg38_scope'),('finalize_ifc_upload'),
    ('claim_reg38_ifc_job'),('retry_reg38_ifc_job'),('reg38_schema_health')
  ), missing as (
    select 'table:' || name as item from required_relations
      where to_regclass('public.' || name) is null
    union all
    select 'column:' || relation_name || '.' || column_name as item from required_columns
      where not exists (
        select 1 from information_schema.columns c
        where c.table_schema='public' and c.table_name=relation_name
          and c.column_name=required_columns.column_name
      )
    union all
    select 'function:' || name as item from required_functions
      where not exists (
        select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        where n.nspname='public' and p.proname=required_functions.name
      )
  )
  select jsonb_build_object(
    'valid', not exists(select 1 from missing),
    'missing', coalesce((select jsonb_agg(item order by item) from missing), '[]'::jsonb)
  )
$$;
revoke all on function public.reg38_schema_health() from public, anon;
grant execute on function public.reg38_schema_health() to authenticated;
