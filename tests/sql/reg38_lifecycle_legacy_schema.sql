-- Emulate a local/legacy deployment which already applied the lifecycle DDL
-- without recording the migration.  Applying (and replaying) the repository
-- migration must retain this policy rather than trying to recreate it.
alter table public.projects
  add column spatial_ifc_unavailable boolean not null default false,
  add column spatial_ifc_acknowledged_at timestamptz,
  add column spatial_ifc_acknowledged_by uuid references auth.users(id) on delete set null;

create table public.reg38_project_audit_events (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  actor_id uuid references auth.users(id) on delete set null,
  event_type text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

alter table public.reg38_project_audit_events enable row level security;
create policy reg38_audit_select
  on public.reg38_project_audit_events
  for select
  to authenticated
  using (public.is_project_member(project_id));
