-- Exercise the production RPC with the legacy display constraint installed.
alter table public.reg38_sections drop constraint if exists reg38_sections_status_check;
alter table public.reg38_sections add constraint reg38_sections_status_check check (
  status = any (array[
    'Not Started', 'In Progress', 'Ready for Review', 'Complete', 'Not Applicable'
  ])
);

create or replace function auth.uid() returns uuid language sql stable as $$
  select '10000000-0000-4000-8000-000000000038'::uuid
$$;

insert into auth.users(id, raw_user_meta_data) values
  ('10000000-0000-4000-8000-000000000038', '{"full_name":"FireTrace owner"}');
update public.profiles
set security_role='SUPER_ADMIN'
where id='10000000-0000-4000-8000-000000000038';

do $$
declare
  created_project uuid;
begin
  created_project := public.create_reg38_project(jsonb_build_object(
    'name', 'FireTrace regression project',
    'project_reference', 'FT-038',
    'country', 'United Kingdom'
  ));

  if not exists (select 1 from public.projects where id=created_project) then
    raise exception 'create_reg38_project did not create a project';
  end if;
  if not exists (select 1 from public.project_members
      where project_id=created_project
        and user_id='10000000-0000-4000-8000-000000000038' and role='OWNER') then
    raise exception 'create_reg38_project did not create owner membership';
  end if;
  if (select count(*) from public.reg38_sections where project_id=created_project) <> 17 then
    raise exception 'create_reg38_project did not create 17 sections';
  end if;
  if exists (select 1 from public.reg38_sections where project_id=created_project
      and (status <> 'Not Started' or completion_status <> 'NOT_STARTED')) then
    raise exception 'new sections have inconsistent status defaults';
  end if;
end $$;
