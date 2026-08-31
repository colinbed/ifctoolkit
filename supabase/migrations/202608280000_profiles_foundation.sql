-- Repository-owned prerequisite for the application profile migrations.
-- Supabase supplies auth.users, but public.profiles is application schema and
-- must be reproducible without a Dashboard-created table.
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

