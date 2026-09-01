-- A source finding has its own stable retry identity. Manual findings may leave
-- this null; worker-extracted findings always populate it.
alter table public.fire_requirements
  add column if not exists source_finding_key text;

create unique index if not exists fire_requirements_source_finding_key_uidx
  on public.fire_requirements(source_finding_key);

-- This independently protects the logical source identity (including legacy
-- worker rows which predate source_finding_key). NULLS NOT DISTINCT makes a
-- missing property set behave as one value rather than bypassing uniqueness.
create unique nulls not distinct index if not exists fire_requirements_logical_finding_uidx
  on public.fire_requirements (
    project_id, ifc_object_id, requirement_type, source_scope,
    source_property_set, source_property_name, source_property_value, source_type
  );

comment on column public.fire_requirements.source_finding_key is
  'Deterministic UUIDv5 of IFC file, object, requirement, scope, pset, property name and value.';
