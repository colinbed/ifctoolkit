# Regulation 38 IFC worker deployment

The model scan must run as a **second Railway service**, built from this same
repository. It must not share the web process lifecycle or an HTTP timeout.

## Start command

```bash
python -m backend.reg38_ifc_worker
```

## Environment

Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` on the worker only. The
service-role key must never be exposed to the browser. `REG38_WORKER_POLL_SECONDS`
is optional and defaults to `3`.

## Runtime sizing

Start with one worker process per service and one IFC job at a time. Scale by
adding worker replicas: `claim_reg38_ifc_job()` uses `FOR UPDATE SKIP LOCKED` to
prevent duplicate claims. Use at least 4 GB RAM (8 GB for geometrically complex
or near-500 MB models), 2 vCPU, and temporary disk capacity of at least twice
the maximum uploaded IFC size. The worker streams Storage downloads to a unique
temporary directory and removes that directory after success or failure.

Do not configure an HTTP request timeout for scans; the worker has no inbound
scan request. Allow jobs to run for at least 60 minutes and use Railway restart
handling for process failures. The original remains in the private
`project-files` bucket; ephemeral Railway disk is only scratch space.
