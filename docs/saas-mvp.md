# IFC Toolkit SaaS MVP

The existing FastAPI validation toolkit exposes a public marketing surface and a private, Supabase-authenticated application shell. Supabase Auth owns account credentials and sessions. The application stores only the user's signed, HttpOnly Supabase session cookie and uses the publishable key; it does not use a service-role key.

Uploads remain in temporary session storage and are removed by session expiry or explicit deletion. Original uploads must be deleted after validation; only report metadata and configured report outputs should persist.

## Production configuration

Set `AUTH_SECRET`, `APP_URL`, `SUPABASE_URL`, and `SUPABASE_PUBLISHABLE_KEY` for authentication. Optional processing/storage settings include `DATABASE_URL`, `STORAGE_BUCKET`, `STORAGE_REGION`, `STORAGE_ENDPOINT`, `FILE_RETENTION_MINUTES`, and `MAX_UPLOAD_SIZE_MB`. Locally, values can be placed in repository-root `.env.local`; deployment-provided values take precedence.

Deployment targets are Civo UK Sovereign Cloud for MVP and Azure UK South for enterprise-ready deployments. Do not claim certification unless independently confirmed; approved language is “designed to support Cyber Essentials requirements” and “aligned with ISO 27001 principles.”

## Migration and follow-up

1. Connect the existing project model to authenticated Supabase user IDs and RLS policies.
2. Connect validation workers to project activity and audit events.
3. Add a scheduled retention worker and temporary object-storage adapter.
4. Prepare optional SSO, MFA, custom retention, and subscription billing.
