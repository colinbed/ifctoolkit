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

echo "Fresh and legacy Regulation 38 migration chains passed."
