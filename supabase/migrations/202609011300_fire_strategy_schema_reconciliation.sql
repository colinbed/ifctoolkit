-- Reconcile the Fire Strategy schema after two migrations were shipped with
-- version 202609010001. Production recorded the earlier lifecycle migration,
-- so Supabase correctly skipped the later Fire Strategy file with that version.

alter table public.project_spaces
  add column if not exists working_fields_edited boolean not null default false;

-- Keep the full definition here for databases where the relation is wholly
-- absent, then reconcile every column for databases with a partially-created
-- relation. Boolean/array/json defaults safely backfill pre-existing rows.
create table if not exists public.fire_strategy_reviews (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  model_id uuid not null references public.ifc_files(id) on delete cascade,
  ifc_object_id uuid references public.ifc_objects(id) on delete set null,
  ifc_global_id text not null,
  entity_type text not null,
  relevance text not null default 'NOT_ASSESSED',
  categories text[] not null default '{}',
  requirement_reference text,
  required_fire_performance text,
  evidence_required text,
  no_evidence_required boolean not null default false,
  review_notes text,
  responsible_organisation text,
  review_status text not null default 'NOT_STARTED',
  automatically_suggested boolean not null default false,
  manually_selected boolean not null default false,
  suggestion_reason text,
  original_values jsonb not null default '{}',
  orphaned boolean not null default false,
  reviewed_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.fire_strategy_reviews
  add column if not exists id uuid default gen_random_uuid(),
  add column if not exists project_id uuid,
  add column if not exists model_id uuid,
  add column if not exists ifc_object_id uuid,
  add column if not exists ifc_global_id text,
  add column if not exists entity_type text,
  add column if not exists relevance text not null default 'NOT_ASSESSED',
  add column if not exists categories text[] not null default '{}',
  add column if not exists requirement_reference text,
  add column if not exists required_fire_performance text,
  add column if not exists evidence_required text,
  add column if not exists no_evidence_required boolean not null default false,
  add column if not exists review_notes text,
  add column if not exists responsible_organisation text,
  add column if not exists review_status text not null default 'NOT_STARTED',
  add column if not exists automatically_suggested boolean not null default false,
  add column if not exists manually_selected boolean not null default false,
  add column if not exists suggestion_reason text,
  add column if not exists original_values jsonb not null default '{}',
  add column if not exists orphaned boolean not null default false,
  add column if not exists reviewed_by uuid,
  add column if not exists created_at timestamptz not null default now(),
  add column if not exists updated_at timestamptz not null default now();

-- Identity columns cannot be safely invented for rows from an unknown partial
-- deployment. Refuse to bless such a table rather than silently corrupt it.
do $$
begin
  if exists (select 1 from public.fire_strategy_reviews where
      id is null or project_id is null or model_id is null or
      ifc_global_id is null or entity_type is null) then
    raise exception 'fire_strategy_reviews has rows missing required identity values';
  end if;
  alter table public.fire_strategy_reviews alter column id set not null;
  alter table public.fire_strategy_reviews alter column project_id set not null;
  alter table public.fire_strategy_reviews alter column model_id set not null;
  alter table public.fire_strategy_reviews alter column ifc_global_id set not null;
  alter table public.fire_strategy_reviews alter column entity_type set not null;
end $$;

update public.fire_strategy_reviews set
  relevance=coalesce(relevance, 'NOT_ASSESSED'),
  categories=coalesce(categories, '{}'),
  no_evidence_required=coalesce(no_evidence_required, false),
  review_status=coalesce(review_status, 'NOT_STARTED'),
  automatically_suggested=coalesce(automatically_suggested, false),
  manually_selected=coalesce(manually_selected, false),
  original_values=coalesce(original_values, '{}'),
  orphaned=coalesce(orphaned, false),
  created_at=coalesce(created_at, now()),
  updated_at=coalesce(updated_at, now());

alter table public.fire_strategy_reviews
  alter column id set default gen_random_uuid(),
  alter column relevance set default 'NOT_ASSESSED', alter column relevance set not null,
  alter column categories set default '{}', alter column categories set not null,
  alter column no_evidence_required set default false, alter column no_evidence_required set not null,
  alter column review_status set default 'NOT_STARTED', alter column review_status set not null,
  alter column automatically_suggested set default false, alter column automatically_suggested set not null,
  alter column manually_selected set default false, alter column manually_selected set not null,
  alter column original_values set default '{}', alter column original_values set not null,
  alter column orphaned set default false, alter column orphaned set not null,
  alter column created_at set default now(), alter column created_at set not null,
  alter column updated_at set default now(), alter column updated_at set not null;

do $$
begin
  if not exists (select 1 from pg_constraint where conrelid='public.fire_strategy_reviews'::regclass and contype='p') then
    alter table public.fire_strategy_reviews add constraint fire_strategy_reviews_pkey primary key (id);
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.fire_strategy_reviews'::regclass and conname='fire_strategy_reviews_project_id_fkey') then
    alter table public.fire_strategy_reviews add constraint fire_strategy_reviews_project_id_fkey foreign key (project_id) references public.projects(id) on delete cascade;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.fire_strategy_reviews'::regclass and conname='fire_strategy_reviews_model_id_fkey') then
    alter table public.fire_strategy_reviews add constraint fire_strategy_reviews_model_id_fkey foreign key (model_id) references public.ifc_files(id) on delete cascade;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.fire_strategy_reviews'::regclass and conname='fire_strategy_reviews_ifc_object_id_fkey') then
    alter table public.fire_strategy_reviews add constraint fire_strategy_reviews_ifc_object_id_fkey foreign key (ifc_object_id) references public.ifc_objects(id) on delete set null;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.fire_strategy_reviews'::regclass and conname='fire_strategy_reviews_reviewed_by_fkey') then
    alter table public.fire_strategy_reviews add constraint fire_strategy_reviews_reviewed_by_fkey foreign key (reviewed_by) references auth.users(id);
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.fire_strategy_reviews'::regclass and conname='fire_strategy_reviews_relevance_check') then
    alter table public.fire_strategy_reviews add constraint fire_strategy_reviews_relevance_check
      check (relevance in ('IN_SCOPE','OUT_OF_SCOPE','REVIEW_REQUIRED','NOT_ASSESSED'));
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.fire_strategy_reviews'::regclass and conname='fire_strategy_reviews_review_status_check') then
    alter table public.fire_strategy_reviews add constraint fire_strategy_reviews_review_status_check
      check (review_status in ('NOT_STARTED','IN_PROGRESS','READY_FOR_REVIEW','APPROVED','REJECTED','NOT_APPLICABLE'));
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.fire_strategy_reviews'::regclass and conname='fire_strategy_reviews_project_id_model_id_ifc_global_id_key') then
    alter table public.fire_strategy_reviews add constraint fire_strategy_reviews_project_id_model_id_ifc_global_id_key
      unique (project_id, model_id, ifc_global_id);
  end if;
end $$;

create index if not exists fire_strategy_reviews_project_model_idx
  on public.fire_strategy_reviews(project_id, model_id);

drop trigger if exists fire_strategy_reviews_set_updated_at on public.fire_strategy_reviews;
create trigger fire_strategy_reviews_set_updated_at before update on public.fire_strategy_reviews
  for each row execute function public.set_updated_at();

alter table public.fire_strategy_reviews enable row level security;
do $$
begin
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='fire_strategy_reviews' and policyname='fire_strategy_reviews_select') then
    create policy fire_strategy_reviews_select on public.fire_strategy_reviews
      for select using (public.is_project_member(project_id));
  end if;
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='fire_strategy_reviews' and policyname='fire_strategy_reviews_write') then
    create policy fire_strategy_reviews_write on public.fire_strategy_reviews for all
      using (public.can_edit_project(project_id)) with check (public.can_edit_project(project_id));
  end if;
end $$;
grant select, insert, update, delete on public.fire_strategy_reviews to authenticated;

-- Extend the latest health function: a recorded migration version is not proof
-- that the relations and worker/API contract actually exist.
create or replace function public.reg38_schema_health() returns jsonb
language sql stable security definer set search_path=public as $$
  with required_relations(name) as (values
    ('profiles'),('projects'),('project_members'),('reg38_project_scope'),('reg38_sections'),
    ('ifc_files'),('ifc_processing_jobs'),('buildings'),('building_storeys'),
    ('ifc_objects'),('ifc_object_properties'),('ifc_object_relationships'),
    ('project_spaces'),('project_zones'),('project_zone_members'),
    ('project_grids'),('project_grid_axes'),('fire_requirements'),('model_scan_warnings'),
    ('fire_strategy_reviews')
  ), required_columns(relation_name,column_name) as (values
    ('ifc_objects','id'),('ifc_objects','project_id'),('ifc_objects','ifc_file_id'),
    ('ifc_objects','ifc_global_id'),('ifc_objects','ifc_entity'),
    ('model_scan_warnings','id'),('model_scan_warnings','project_id'),
    ('model_scan_warnings','ifc_file_id'),('model_scan_warnings','ifc_object_id'),
    ('model_scan_warnings','warning_code'),('model_scan_warnings','review_status'),
    ('project_spaces','id'),('project_spaces','space_number'),('project_spaces','name'),
    ('project_spaces','description'),('project_spaces','occupancy_type'),
    ('project_spaces','occupancy_capacity'),('project_spaces','high_risk'),
    ('project_spaces','included_in_reg38'),('project_spaces','working_geometry'),
    ('project_spaces','working_fields_edited'),
    ('fire_strategy_reviews','id'),('fire_strategy_reviews','project_id'),
    ('fire_strategy_reviews','model_id'),('fire_strategy_reviews','ifc_object_id'),
    ('fire_strategy_reviews','ifc_global_id'),('fire_strategy_reviews','entity_type'),
    ('fire_strategy_reviews','relevance'),('fire_strategy_reviews','categories'),
    ('fire_strategy_reviews','review_status'),('fire_strategy_reviews','original_values'),
    ('fire_strategy_reviews','orphaned'),('fire_strategy_reviews','reviewed_by'),
    ('fire_strategy_reviews','updated_at')
  ), required_functions(name) as (values
    ('is_project_member'),('can_edit_project'),('can_manage_project'),
    ('create_reg38_project'),('save_reg38_scope'),('finalize_ifc_upload'),
    ('claim_reg38_ifc_job'),('retry_reg38_ifc_job'),('reg38_schema_health')
  ), missing as (
    select 'table:' || name as item from required_relations where to_regclass('public.' || name) is null
    union all
    select 'column:' || relation_name || '.' || column_name from required_columns
      where not exists (select 1 from information_schema.columns c where c.table_schema='public'
        and c.table_name=relation_name and c.column_name=required_columns.column_name)
    union all
    select 'function:' || name from required_functions where not exists
      (select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
       where n.nspname='public' and p.proname=required_functions.name)
    union all
    select 'default:reg38_sections.status' where
      (select column_default from information_schema.columns where table_schema='public'
       and table_name='reg38_sections' and column_name='status') is distinct from '''Not Started''::text'
    union all
    select 'default:reg38_sections.completion_status' where
      (select column_default from information_schema.columns where table_schema='public'
       and table_name='reg38_sections' and column_name='completion_status') is distinct from '''NOT_STARTED''::text'
    union all
    select 'constraint:reg38_sections.status' where exists (
      select 1 from pg_constraint c join pg_class t on t.oid=c.conrelid
      join pg_namespace n on n.oid=t.relnamespace
      join pg_attribute a on a.attrelid=t.oid and a.attname='status'
      where n.nspname='public' and t.relname='reg38_sections' and c.contype='c'
        and a.attnum=any(c.conkey) and pg_get_constraintdef(c.oid) not like '%Not Started%')
    union all
    select 'data:reg38_sections.status' where exists (select 1 from public.reg38_sections where status not in
      ('Not Started','In Progress','Ready for Review','Complete','Not Applicable'))
  )
  select jsonb_build_object('valid', not exists(select 1 from missing),
    'missing', coalesce((select jsonb_agg(item order by item) from missing), '[]'::jsonb))
$$;
revoke all on function public.reg38_schema_health() from public, anon;
grant execute on function public.reg38_schema_health() to authenticated;

-- Ask PostgREST to refresh immediately rather than waiting for its cache timer.
notify pgrst, 'reload schema';

-- Executable smoke checks: migration application fails if either API relation
-- cannot be selected with its required shape.
select working_fields_edited from public.project_spaces limit 1;
select * from public.fire_strategy_reviews limit 1;
