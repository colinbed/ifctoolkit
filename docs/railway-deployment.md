# Railway production deployment

Railway is IFC Toolkit's primary production platform. Railway builds the root `Dockerfile` from GitHub and starts FastAPI on `0.0.0.0` using its injected `PORT`. The GitHub Actions workflow is CI-only; it neither publishes GHCR images nor deploys infrastructure.

## Connect and deploy

1. In Railway, create a project and select **Deploy from GitHub repo**.
2. Select `colinbed/ifctoolkit` and the `main` branch. Railway uses `railway.json` and the root `Dockerfile`.
3. Add the variables below. Values belong in Railway Variables, never in Git or an environment file.
4. Generate a Railway domain. Set `APP_URL` to that HTTPS origin, without a trailing slash, and redeploy.
5. In Supabase **Authentication → URL Configuration**, set Site URL to the same `APP_URL` and allow exactly `<APP_URL>/auth/callback`. This callback handles confirmation and password recovery. Add both the Railway and custom-domain callbacks during a domain transition.
6. Verify `GET /health/live`, `/health/ready`, and `/health/build-info`, then test sign-up, confirmation, sign-in, reset, account access, and sign-out.
7. To adopt a custom domain, configure it in Railway first, add its callback in Supabase, change `APP_URL`, and redeploy. DNS changes and retirement of Civo are separate operator actions.

## Railway Variables

Required for a production-ready service:

| Variable | Safe value / purpose |
| --- | --- |
| `AUTH_SECRET` | A unique, high-entropy signing secret generated in a secret manager. |
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `SUPABASE_PUBLISHABLE_KEY` | Supabase publishable/anon client key; **never** a service-role key. |
| `APP_URL` | `https://<service>.up.railway.app` initially, later the canonical custom HTTPS origin. |

Recommended runtime controls:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAX_UPLOAD_SIZE_MB` | `1200` | General application upload limit in MiB. Size the Railway service disk/memory accordingly. |
| `APP_TEMP_ROOT` | `/tmp/ifctoolkit` | Temporary session, upload, and generated-output root. |
| `FILE_RETENTION_MINUTES` | `360` | Temporary file TTL; valid range is 5–1440 minutes. |
| `MIN_READY_TEMP_FREE_BYTES` | `536870912` | Free disk threshold used by `/health/ready`. |
| `SUPABASE_AUTH_TIMEOUT_SECONDS` | `10` | Supabase HTTP timeout. |
| `DEPLOYED_GIT_SHA` | unset | Optional source revision exposed by build-info; Railway may map a commit variable to it. |

Feature-specific variables remain supported and should be added only when that feature is operated: `DATABASE_URL` plus `IFC_MAX_TOTAL_BYTES`, `IFC_MAX_FILES_PER_JOB`, `IFC_JOB_TIMEOUT_SECONDS`, and `IFC_WORKER_CONCURRENCY` for the database-backed IFC queue; `IFC_OUTPUT_BUCKET`/storage settings for external outputs; and the documented `COBIEQC_*` variables when selecting optional Java COBieQC resources. The default native Python COBie checker does not require Java assets. `PORT` is supplied by Railway and must not be set manually.

## Temporary processing and scaling

Uploads and generated IFC, Excel, and COBie artifacts remain temporary. They use Railway's ephemeral container filesystem at `APP_TEMP_ROOT`; cleanup runs according to `FILE_RETENTION_MINUTES`, and readiness checks that the location is writable and has `MIN_READY_TEMP_FREE_BYTES` available. A redeploy, restart onto another container, or service replacement can discard active local sessions. This is expected for temporary files and no Railway Volume is required.

Run one web replica while session files and in-memory job state are local. A persistent Railway Volume alone would not make that state safe for horizontal scaling; durable multi-replica operation requires an external session/object store and shared job state. Use a Volume only if product requirements deliberately change artifacts from temporary to durable.

## Health behavior

Railway checks `/health/live`, which confirms that the process is serving requests. `/health/ready` is stricter: it returns 503 if Supabase authentication variables are missing, the temp root is unwritable, or free disk is below the configured threshold. This separation prevents an initial configuration error from causing an endless platform restart loop while still exposing it to operators.

## Legacy infrastructure

Former Kubernetes manifests are archived under `docs/legacy-kubernetes/`, and the old Civo runbook is explicitly deprecated. They are not referenced by Railway, CI, the Docker build, or application startup. Removing the live Civo cluster, old GitHub secrets/packages, and DNS records must be performed separately after the Railway deployment is validated.
