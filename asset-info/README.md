# Asset Information Assurance

An isolated FastAPI service for future asset-information assurance capabilities. It has no runtime dependency on the existing IFCToolkit application.

## Local development

```bash
docker compose up --build
curl http://localhost:8001/health/live
```

The Compose project starts the `asset-info-api` and its own PostgreSQL service and persistent volume. Apply migrations with:

```bash
docker compose run --rm asset-info-api alembic upgrade head
```

Run tests locally from this directory with `pytest`. Set `DATABASE_URL` to override the default PostgreSQL connection string.

## Scope

This initial scaffold includes health checks and core database entities only. IFC ingestion, drawings, Glider integration, authentication, UI, and assurance rules are intentionally not implemented.
