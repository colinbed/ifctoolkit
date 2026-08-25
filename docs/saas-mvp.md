# IFC Toolkit SaaS application

IFC Toolkit exposes a public marketing site, the existing IFC/COBie tools, and a private application workspace. Supabase Auth is the single authentication provider. Browser access and refresh tokens are stored in a signed, HttpOnly, SameSite cookie and validated against Supabase before private pages render. The former SQLite user/password store and its middleware are obsolete and must not be restored.

The implemented authentication routes cover sign-up, sign-in, sign-out, email confirmation callbacks, forgotten-password emails, password reset, current-user template context, private-page redirects, and profile updates. Account display names are stored in Supabase Auth user metadata. If a `public.profiles` row already exists, the account page also reads and updates its available name column using the signed-in user's access token.

## Required authentication configuration

Set these values through the process environment or deployment secret manager:

- `AUTH_SECRET`: strong random key for signing the browser session cookie;
- `APP_URL`: canonical public origin, without a trailing slash;
- `SUPABASE_URL`: Supabase project URL;
- `SUPABASE_PUBLISHABLE_KEY`: publishable client key, never a service-role key;
- `SUPABASE_AUTH_TIMEOUT_SECONDS`: optional request timeout, default `10`.

In Supabase Auth URL Configuration, set the Site URL to `APP_URL` and allow `<APP_URL>/auth/callback`. Email confirmation and password-recovery links return to that callback. If `public.profiles` is exposed through the Data API, enable row-level security and ownership policies so authenticated users can select and update only their own row.

Local `.env.local` loading is supported for development and never overwrites values already supplied by the process. `.env*` files are ignored, excluded from the container build context, and rejected by the tracked-secret filename check; do not add a tracked environment example file.

## Kubernetes deployment

`k8s/deployment.yaml` reads `AUTH_SECRET`, `SUPABASE_URL`, and `SUPABASE_PUBLISHABLE_KEY` from the pre-existing Kubernetes Secret `ifctoolkit-auth`. Create and rotate that Secret out of band. The deployment workflow checks that the Secret exists but never reads or prints its values.

Uploads remain in temporary session storage and are removed by the configured retention cleanup or explicit deletion. Original uploads must not be persisted as account records. Keep the deployment at one replica until uploaded files and in-memory job state are moved to external storage.

## Follow-up boundaries

1. Add durable Supabase-backed project and organisation data with explicit RLS policies.
2. Move session files and job state to external storage before horizontal scaling.
3. Add a scheduled retention process for strict time-based deletion during idle periods.
4. Add MFA/SSO and subscription billing when the product requirements are defined.
