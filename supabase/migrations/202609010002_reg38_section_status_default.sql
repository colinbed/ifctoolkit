-- Keep the legacy, human-readable status field compatible with its production
-- check constraint. completion_status remains the canonical machine state.
do $$
begin
  if exists (
    select 1 from public.reg38_sections
    where status not in (
      'Not Started', 'In Progress', 'Ready for Review', 'Complete', 'Not Applicable'
    )
  ) then
    raise exception 'reg38_sections contains invalid legacy status values';
  end if;
end $$;

alter table public.reg38_sections
  alter column status set default 'Not Started';

alter table public.reg38_sections
  alter column completion_status set default 'NOT_STARTED';

-- Extend the deployment diagnostic so a future default/constraint mismatch is
-- visible before it breaks create_reg38_project. A missing legacy status check
-- is tolerated, but any check involving status must accept "Not Started".
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
    select 'column:' || relation_name || '.' || column_name from required_columns
      where not exists (select 1 from information_schema.columns c
        where c.table_schema='public' and c.table_name=relation_name
          and c.column_name=required_columns.column_name)
    union all
    select 'function:' || name from required_functions
      where not exists (select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
        where n.nspname='public' and p.proname=required_functions.name)
    union all
    select 'default:reg38_sections.status' where
      (select column_default from information_schema.columns
       where table_schema='public' and table_name='reg38_sections' and column_name='status')
      is distinct from '''Not Started''::text'
    union all
    select 'default:reg38_sections.completion_status' where
      (select column_default from information_schema.columns
       where table_schema='public' and table_name='reg38_sections' and column_name='completion_status')
      is distinct from '''NOT_STARTED''::text'
    union all
    select 'constraint:reg38_sections.status' where exists (
      select 1 from pg_constraint c
      join pg_class t on t.oid=c.conrelid
      join pg_namespace n on n.oid=t.relnamespace
      join pg_attribute a on a.attrelid=t.oid and a.attname='status'
      where n.nspname='public' and t.relname='reg38_sections' and c.contype='c'
        and a.attnum=any(c.conkey) and pg_get_constraintdef(c.oid) not like '%Not Started%'
    )
    union all
    select 'data:reg38_sections.status' where exists (
      select 1 from public.reg38_sections where status not in
        ('Not Started','In Progress','Ready for Review','Complete','Not Applicable')
    )
  )
  select jsonb_build_object(
    'valid', not exists(select 1 from missing),
    'missing', coalesce((select jsonb_agg(item order by item) from missing), '[]'::jsonb)
  )
$$;

revoke all on function public.reg38_schema_health() from public, anon;
grant execute on function public.reg38_schema_health() to authenticated;
