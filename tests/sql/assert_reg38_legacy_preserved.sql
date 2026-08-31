do $$
begin
  if (select count(*) from public.projects where id='20000000-0000-4000-8000-000000000001') <> 1
     or (select count(*) from public.project_members where id='30000000-0000-4000-8000-000000000001') <> 1
     or (select count(*) from public.buildings where id='40000000-0000-4000-8000-000000000001') <> 1
     or (select count(*) from public.building_storeys where id='50000000-0000-4000-8000-000000000001') <> 1
     or (select count(*) from public.ifc_files where id='60000000-0000-4000-8000-000000000001') <> 1
     or (select count(*) from public.ifc_processing_jobs where id='70000000-0000-4000-8000-000000000001') <> 1
     or (select count(*) from public.ifc_objects where id='80000000-0000-4000-8000-000000000001') <> 1
     or (select count(*) from public.ifc_object_properties where id='90000000-0000-4000-8000-000000000001') <> 1 then
    raise exception 'canonical migrations lost or re-keyed legacy Regulation 38 data';
  end if;

  if not exists (select 1 from information_schema.columns
                 where table_schema='public' and table_name='projects' and column_name='archived_at')
     or not exists (select 1 from information_schema.columns
                    where table_schema='public' and table_name='ifc_objects' and column_name='geometry_metadata')
     or not exists (select 1 from pg_class where relname='ifc_objects_entity_idx') then
    raise exception 'legacy schema was not fully reconciled';
  end if;
end $$;
