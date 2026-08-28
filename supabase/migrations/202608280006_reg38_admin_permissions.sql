-- Finalize canonical platform authorization and expose deployment diagnostics.
-- 202608280005 creates these columns; IF NOT EXISTS also makes this safe to retry.
alter table public.profiles add column if not exists security_role text not null default 'MEMBER';
alter table public.profiles add column if not exists can_create_projects boolean not null default false;
alter table public.profiles drop constraint if exists profiles_security_role_check;
alter table public.profiles add constraint profiles_security_role_check
  check(security_role in ('MEMBER','ADMIN','SUPER_ADMIN'));

-- Some development databases had account_level; the four-migration production
-- baseline may not. Promote legacy admins only when that column really exists.
do $$
begin
  if exists(select 1 from information_schema.columns where table_schema='public'
      and table_name='profiles' and column_name='account_level') then
    execute $sql$update public.profiles set security_role = 'SUPER_ADMIN'
      where account_level = 'admin' and security_role = 'MEMBER'$sql$;
  end if;
end $$;

create or replace function public.is_platform_admin() returns boolean
language sql stable security definer set search_path=public as $$
  select exists(select 1 from public.profiles
    where id=(select auth.uid()) and security_role = 'SUPER_ADMIN')
$$;
create or replace function public.can_create_project() returns boolean
language sql stable security definer set search_path=public as $$
  select exists(select 1 from public.profiles where id=(select auth.uid())
    and (security_role = 'SUPER_ADMIN' or (security_role = 'ADMIN' and can_create_projects)))
$$;

create or replace function public.reg38_schema_health() returns jsonb
language sql stable security definer set search_path=public as $$
  with required(table_name,column_name) as (values
    ('projects','id'),('projects','name'),('projects','building_name'),('projects','client_name'),
    ('projects','principal_contractor'),('projects','principal_designer'),('projects','building_type'),
    ('projects','project_status'),('projects','planned_handover_date'),('projects','responsible_person_name'),
    ('projects','responsible_person_email'),('projects','address_line_1'),('projects','address_line_2'),
    ('projects','town_city'),('projects','county'),('projects','postcode'),('projects','country'),
    ('projects','created_by'),('projects','archived_at'),('project_members','project_id'),
    ('project_members','created_at'),('reg38_sections','enabled'),('reg38_sections','sort_order'),
    ('reg38_sections','completion_status'),('reg38_sections','applicability_status'),
    ('reg38_project_scope','project_id'),('ifc_files','project_id'),('ifc_processing_jobs','project_id'),
    ('profiles','security_role'),('profiles','can_create_projects')
  ), missing as (
    select r.table_name || '.' || r.column_name item from required r
    where not exists(select 1 from information_schema.columns c where c.table_schema='public'
      and c.table_name=r.table_name and c.column_name=r.column_name)
  ) select jsonb_build_object('valid',not exists(select 1 from missing),
      'missing',coalesce((select jsonb_agg(item order by item) from missing),'[]'::jsonb))
$$;

revoke all on function public.is_platform_admin(),public.can_create_project(),public.reg38_schema_health() from public;
grant execute on function public.is_platform_admin(),public.can_create_project(),public.reg38_schema_health() to authenticated;
