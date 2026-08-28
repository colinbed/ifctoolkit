-- Account entitlements and automatic 90-day Premium-feature trial.
alter table public.profiles
  add column if not exists account_level text not null default 'standard',
  add column if not exists trial_started_at timestamptz,
  add column if not exists trial_ends_at timestamptz,
  add column if not exists subscription_status text not null default 'coming_soon';

alter table public.profiles drop constraint if exists profiles_account_level_check;
alter table public.profiles add constraint profiles_account_level_check check (account_level in ('standard','premium','admin'));
alter table public.profiles drop constraint if exists profiles_subscription_status_check;
alter table public.profiles add constraint profiles_subscription_status_check check (subscription_status in ('trial','active','expired','coming_soon','cancelled'));

create or replace function public.handle_new_user() returns trigger language plpgsql security definer
set search_path = public as $$
begin
  insert into public.profiles (id, full_name, account_level, trial_started_at, trial_ends_at, subscription_status)
  values (new.id, coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name', ''),
          'standard', now(), now() + interval '90 days', 'trial')
  on conflict (id) do nothing;
  return new;
end; $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users for each row execute procedure public.handle_new_user();

alter table public.profiles enable row level security;
drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own" on public.profiles for select to authenticated using ((select auth.uid()) = id);
-- Intentionally no browser INSERT/UPDATE policy: entitlement fields are server/admin controlled.
-- Existing users retain the safe Standard fallback. Grant trials manually if a backfill is desired.
