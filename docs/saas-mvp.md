# IFC Toolkit SaaS MVP

The existing FastAPI validation toolkit now exposes a public marketing surface and a private, session-backed application shell. The MVP account store defaults to SQLite (`SAAS_DB_PATH`) and prepares users, organisations, memberships, validation jobs, reports, and audit logs. Production deployments should migrate these entities to the service configured by `DATABASE_URL`.

Uploads remain in temporary session storage and are removed by session expiry or explicit deletion. Original uploads must be deleted after validation; only report metadata and configured report outputs should persist.

## Production configuration

Set `AUTH_SECRET`, `APP_URL`, `DATABASE_URL`, `STORAGE_BUCKET`, `STORAGE_REGION`, `STORAGE_ENDPOINT`, `FILE_RETENTION_MINUTES`, and `MAX_UPLOAD_SIZE_MB`. `STRIPE_SECRET_KEY` and `EMAIL_PROVIDER_KEY` are placeholders until those integrations are enabled.

Deployment targets are Civo UK Sovereign Cloud for MVP and Azure UK South for enterprise-ready deployments. Do not claim certification unless independently confirmed; approved language is “designed to support Cyber Essentials requirements” and “aligned with ISO 27001 principles.”

## Migration and follow-up

1. Replace SQLite with managed PostgreSQL migrations.
2. Connect validation workers to validation jobs and audit events.
3. Add a scheduled retention worker and temporary object-storage adapter.
4. Connect email verification/password reset, then add CSRF tokens to privileged forms.
5. Prepare optional SSO, MFA, custom retention, and subscription billing.
