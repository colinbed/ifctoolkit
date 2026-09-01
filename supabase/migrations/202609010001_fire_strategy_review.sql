-- Canonical IfcSpace working-field audit and durable Fire Strategy review scope.
alter table public.project_spaces
  add column if not exists working_fields_edited boolean not null default false;

create table if not exists public.fire_strategy_reviews (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  model_id uuid not null references public.ifc_files(id) on delete cascade,
  ifc_object_id uuid references public.ifc_objects(id) on delete set null,
  ifc_global_id text not null,
  entity_type text not null,
  relevance text not null default 'NOT_ASSESSED'
    check (relevance in ('IN_SCOPE','OUT_OF_SCOPE','REVIEW_REQUIRED','NOT_ASSESSED')),
  categories text[] not null default '{}',
  requirement_reference text,
  required_fire_performance text,
  evidence_required text,
  no_evidence_required boolean not null default false,
  review_notes text,
  responsible_organisation text,
  review_status text not null default 'NOT_STARTED'
    check (review_status in ('NOT_STARTED','IN_PROGRESS','READY_FOR_REVIEW','APPROVED','REJECTED','NOT_APPLICABLE')),
  automatically_suggested boolean not null default false,
  manually_selected boolean not null default false,
  suggestion_reason text,
  original_values jsonb not null default '{}',
  orphaned boolean not null default false,
  reviewed_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, model_id, ifc_global_id)
);
create index if not exists fire_strategy_reviews_project_model_idx
  on public.fire_strategy_reviews(project_id, model_id);
drop trigger if exists fire_strategy_reviews_set_updated_at on public.fire_strategy_reviews;
create trigger fire_strategy_reviews_set_updated_at before update on public.fire_strategy_reviews
  for each row execute function public.set_updated_at();

alter table public.fire_strategy_reviews enable row level security;
drop policy if exists fire_strategy_reviews_select on public.fire_strategy_reviews;
create policy fire_strategy_reviews_select on public.fire_strategy_reviews for select using (public.is_project_member(project_id));
drop policy if exists fire_strategy_reviews_write on public.fire_strategy_reviews;
create policy fire_strategy_reviews_write on public.fire_strategy_reviews for all
  using (public.can_edit_project(project_id)) with check (public.can_edit_project(project_id));
grant select, insert, update, delete on public.fire_strategy_reviews to authenticated;
