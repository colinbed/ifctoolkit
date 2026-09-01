#!/usr/bin/env bash
set -euo pipefail

# Requires a PostgreSQL server and a superuser connection (configured with the
# standard PGHOST/PGPORT/PGUSER variables) because the bootstrap emulates
# Supabase-owned auth/storage schemas and roles.
PSQL=(psql -v ON_ERROR_STOP=1)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PREFIX="reg38_migration_test_$$"

cleanup() {
  "${PSQL[@]}" -c "drop database if exists ${DB_PREFIX}_fresh" >/dev/null
  "${PSQL[@]}" -c "drop database if exists ${DB_PREFIX}_legacy" >/dev/null
  "${PSQL[@]}" -c "drop database if exists ${DB_PREFIX}_lifecycle_legacy" >/dev/null
  "${PSQL[@]}" -c "drop database if exists ${DB_PREFIX}_fire_drift" >/dev/null
}
trap cleanup EXIT

bootstrap() {
  local database="$1"
  "${PSQL[@]}" -c "create database $database" >/dev/null
  psql -v ON_ERROR_STOP=1 -d "$database" <<'SQL' >/dev/null
create schema auth;
create schema storage;
do $$ begin create role anon nologin; exception when duplicate_object then null; end $$;
do $$ begin create role authenticated nologin; exception when duplicate_object then null; end $$;
do $$ begin create role service_role nologin; exception when duplicate_object then null; end $$;
create table auth.users(id uuid primary key, raw_user_meta_data jsonb default '{}'::jsonb);
create function auth.uid() returns uuid language sql stable as $$ select null::uuid $$;
create table storage.buckets(id text primary key, name text not null, public boolean not null default false,
  file_size_limit bigint, allowed_mime_types text[]);
create table storage.objects(id uuid primary key default gen_random_uuid(), bucket_id text references storage.buckets(id), name text not null);
SQL
}

apply_all() {
  local database="$1" migration
  for migration in "$ROOT"/supabase/migrations/*.sql; do
    psql -v ON_ERROR_STOP=1 -d "$database" -f "$migration" >/dev/null
  done
}

bootstrap "${DB_PREFIX}_fresh"
apply_all "${DB_PREFIX}_fresh"
psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_fresh" \
  -f "$ROOT/tests/sql/assert_reg38_project_creation.sql" >/dev/null

# Reproduce the production failure against PostgreSQL itself. Two input rows hit
# the same conflict identity in one command, so PostgreSQL must reject the batch
# with the exact cardinality-violation message observed in production.
psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_fresh" <<'SQL' >/dev/null
do $$
begin
  create temporary table duplicate_upsert_reproduction(id integer primary key, value text);
  insert into duplicate_upsert_reproduction values (1, 'existing');
  begin
    insert into duplicate_upsert_reproduction values (1, 'first'), (1, 'second')
      on conflict (id) do update set value=excluded.value;
    raise exception 'expected duplicate upsert to fail';
  exception when cardinality_violation then
    if sqlerrm <> 'ON CONFLICT DO UPDATE command cannot affect row a second time' then
      raise exception 'unexpected PostgreSQL error: %', sqlerrm;
    end if;
  end;
end $$;
SQL

bootstrap "${DB_PREFIX}_legacy"
for migration in "$ROOT"/supabase/migrations/20260828000{0,1}_*.sql; do
  psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_legacy" -f "$migration" >/dev/null
done
psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_legacy" \
  -f "$ROOT/tests/sql/reg38_legacy_production_schema.sql" >/dev/null
for migration in "$ROOT"/supabase/migrations/*.sql; do
  [[ "$migration" < "$ROOT/supabase/migrations/202608280002" ]] && continue
  psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_legacy" -f "$migration" >/dev/null
done
psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_legacy" \
  -f "$ROOT/tests/sql/assert_reg38_legacy_preserved.sql" >/dev/null

# Reproduce an unrecorded local lifecycle deployment whose columns, audit table,
# and RLS policy already exist. The migration must apply and replay successfully.
bootstrap "${DB_PREFIX}_lifecycle_legacy"
for migration in "$ROOT"/supabase/migrations/*.sql; do
  [[ "$migration" > "$ROOT/supabase/migrations/202609010001_fire_strategy_review.sql" ]] && break
  psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_lifecycle_legacy" -f "$migration" >/dev/null
done
psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_lifecycle_legacy" \
  -f "$ROOT/tests/sql/reg38_lifecycle_legacy_schema.sql" >/dev/null
psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_lifecycle_legacy" \
  -f "$ROOT/supabase/migrations/202609010001_reg38_ifc_lifecycle.sql" >/dev/null
psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_lifecycle_legacy" \
  -f "$ROOT/supabase/migrations/202609010001_reg38_ifc_lifecycle.sql" >/dev/null

# Reproduce the exact production history collision: the lifecycle file with
# version 202609010001 was applied, while Supabase skipped the later Fire
# Strategy file carrying the same version. The new reconciliation must repair
# that schema and remain safe if it is evaluated again.
bootstrap "${DB_PREFIX}_fire_drift"
for migration in "$ROOT"/supabase/migrations/*.sql; do
  [[ "$migration" == *202609010001_fire_strategy_review.sql ]] && continue
  [[ "$migration" == *202609011300_fire_strategy_schema_reconciliation.sql ]] && continue
  psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_fire_drift" -f "$migration" >/dev/null
done
psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_fire_drift" \
  -f "$ROOT/supabase/migrations/202609011300_fire_strategy_schema_reconciliation.sql" >/dev/null
psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_fire_drift" \
  -f "$ROOT/supabase/migrations/202609011300_fire_strategy_schema_reconciliation.sql" >/dev/null
psql -v ON_ERROR_STOP=1 -d "${DB_PREFIX}_fire_drift" \
  -f "$ROOT/tests/sql/assert_fire_strategy_reconciliation.sql" >/dev/null

echo "Fresh, legacy, lifecycle replay, and Fire Strategy drift migration chains passed."
