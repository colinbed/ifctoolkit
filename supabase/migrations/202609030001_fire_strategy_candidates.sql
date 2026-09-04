-- Materialise the property part of Model Scan's fire-candidate classification.
-- This turns Fire Strategy page loads into indexed reads instead of repeated
-- substring analysis over every property in the model.
alter table public.ifc_object_properties
  add column if not exists is_fire_relevant boolean not null default false;

create or replace function public.reg38_is_fire_property(
  target_set text, target_name text, target_value text
) returns boolean
language sql immutable parallel safe
set search_path = ''
as $$
  select concat_ws(' ', target_set, target_name, target_value) ~*
    '(fire|smoke|damper|sprinkler|alarm|detection|emergency|escape|compartment|suppression|fire[ _-]?stopping|self[ _-]?closing|resistance|reaction[ _-]?to[ _-]?fire|surface[ _-]?spread|integrity|insulation|(^|[^a-z])(frl|ei|rei)([^a-z]|$))'
$$;

create or replace function public.reg38_mark_fire_property()
returns trigger language plpgsql
set search_path = ''
as $$
begin
  new.is_fire_relevant := public.reg38_is_fire_property(
    new.property_set, new.property_name, new.property_value_text
  );
  return new;
end
$$;

update public.ifc_object_properties
set is_fire_relevant = public.reg38_is_fire_property(
  property_set, property_name, property_value_text
)
where is_fire_relevant is distinct from public.reg38_is_fire_property(
  property_set, property_name, property_value_text
);

drop trigger if exists ifc_object_properties_mark_fire on public.ifc_object_properties;
create trigger ifc_object_properties_mark_fire
before insert or update of property_set, property_name, property_value_text
on public.ifc_object_properties for each row execute function public.reg38_mark_fire_property();

-- These indexes correspond directly to the two staged Fire Strategy queries.
create index if not exists ifc_objects_project_file_entity_idx
  on public.ifc_objects(project_id, ifc_file_id, ifc_entity);
create index if not exists ifc_object_properties_fire_object_idx
  on public.ifc_object_properties(ifc_object_id)
  where is_fire_relevant;

