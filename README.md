# IFC Toolkit

**Practical tools, better compliance.**

IFC Toolkit is a FastAPI-based, SaaS-ready IFC validation product for `ifctoolkit.com`. It combines a public marketing site, private authenticated workspace, organisation-ready account model, and existing IFC/COBie processing utilities.

## Run locally

```bash
cp .env.example .env
uvicorn app:app --reload
```

Open `http://localhost:8000`. Existing prototype utilities remain available from `/legacy/upload` while they are progressively integrated into the authenticated workspace.

Uploaded files are processed in temporary session storage and are intended to be automatically deleted after validation. IFC Toolkit does not use uploaded files to train AI models. Production deployments must set a strong `AUTH_SECRET`, use HTTPS, configure UK-region storage, and run a scheduled retention worker.

See [`docs/saas-mvp.md`](docs/saas-mvp.md) for deployment configuration, migration steps, and follow-up work.
