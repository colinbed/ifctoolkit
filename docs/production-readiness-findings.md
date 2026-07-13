# Production-readiness remediation notes

## Findings-to-fix matrix

| Area | Finding | Status in this change | Remaining action |
| --- | --- | --- | --- |
| Credential incident | A kubeconfig was tracked and Docker copied the full context. | Removed tracked file, added ignore rules, Docker context exclusions and CI filename guard. | Rotate Civo credentials, replace GitHub `KUBE_CONFIG`, purge history only with owner-approved coordination, remove/rebuild affected GHCR images. |
| Public images | Previous GHCR images may include the kubeconfig. | Documented blocker. | Delete/quarantine old images after rotation and rebuild. |
| Local storage | Deployment uses local `emptyDir`. | Preserved one replica, `/tmp` mount and 10 GiB limit; added `Recreate`. | Do not scale until external session/job storage exists. |
| Health checks | No split live/ready endpoints. | Added `/health/live`, `/health/ready`, `/health/build-info`. | Expand smoke tests for full upload flow through ingress. |
| Session TTL | Kubernetes set 30 minutes but app used hard-coded hours. | Added `FILE_RETENTION_MINUTES` parsing and TTL usage. | Complete ownership-cookie and active-job deletion guards in a later security PR. |
| Debug data | Debug endpoints exposed filesystem paths. | Disabled debug endpoints by default and removed `upload_root` from session lookup. | Add admin auth if diagnostics are re-enabled. |
| Non-root container | Container ran as root. | Dockerfile now creates and uses UID/GID 10001. | Evaluate read-only root filesystem after moving static hashing to build time. |
| CI/CD images | Workflow deployed `latest`. | Builds SHA and latest tags; deploys the SHA tag. | Add vulnerability scanning and full ingress upload smoke test. |

## Configuration variables and defaults

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_TEMP_ROOT` | `/tmp/ifctoolkit` | Root for temporary session/job/work directories. |
| `FILE_RETENTION_MINUTES` | `360` in app, `30` in Kubernetes | Session retention TTL. Must be 5-1440 minutes. |
| `MIN_READY_TEMP_FREE_BYTES` | `536870912` | Minimum free temp space required for readiness. |
| `MAX_REQUEST_BODY_BYTES` | `MAX_UPLOAD_BYTES + 25000000` | Middleware content-length request cap. |
| `MAX_UPLOAD_BYTES` | `1200000000` | Current general per-file upload cap. |
| `MAX_UPLOAD_MB` | `50` | COBie-specific upload cap in MiB-equivalent calculation. |
| `HEAVY_JOB_TIMEOUT_SECONDS` | `900` | Heavy job timeout setting. |
| `DEPLOYED_GIT_SHA` | `unknown` in manifest, set by CI | Deployed image source revision exposed by health/build info. |
| `ENABLE_SESSION_DEBUG_ENDPOINTS` | `false` | Disabled-by-default session diagnostics. |

## Temporary disk usage estimates

Multipart uploads are spooled by the ASGI stack before endpoint handling, then copied into the session directory. Generated outputs require additional headroom.

| Scenario | Approximate temporary requirement before outputs | Recommended operational headroom |
| --- | ---: | ---: |
| One 100 MB IFC | ~200 MB (multipart spool + session copy) | 300-500 MB including reports/exports. |
| One 1.2 GB IFC | ~2.4 GB (multipart spool + session copy) | 3.0-4.0 GB depending on output tool. |
| Multiple selected files | ~2x aggregate selected size | Add output headroom and enforce session quota before processing. |

## Session lifecycle statement

- Container restart in the same pod: `emptyDir` under `/tmp` can survive; the app attempts to preserve local session directories until TTL cleanup.
- Pod replacement: sessions and in-memory job state are lost because `emptyDir` is pod-scoped.
- Deployment: `Recreate` avoids two pods serving different local sessions, but the replacement pod starts with empty local storage.
- Node failure: local sessions are lost.

## Future object-storage boundary

Object storage or another external session store is required before enabling more than one replica, rolling deployments, cross-pod session continuity, or durable job recovery. That migration should externalise uploaded files, generated outputs, job metadata and ownership/session metadata together.
