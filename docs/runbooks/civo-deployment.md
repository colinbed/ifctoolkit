# Civo deployment runbook

## Security blocker before deployment

The previously tracked `civo-ifctoolkit-kubeconfig` file contained Kubernetes client-certificate credentials. Do not reuse those credentials.

Before deploying any image from this repository:

1. Rotate the affected Civo Kubernetes credential.
2. Replace the GitHub Actions `KUBE_CONFIG` secret with the rotated kubeconfig.
3. Remove or quarantine prior GHCR image versions that may have been built while the kubeconfig was in the Docker build context.
4. Rebuild images after rotation.
5. Consider Git history purging with repository-owner coordination. Do not force-push shared history without approval.

The workflow now stops before deployment if `secrets.KUBE_CONFIG` is not configured.

## Local-session deployment model

IFC Toolkit currently stores uploads, generated files and in-memory job state locally in the pod. The deployment must remain at one replica until session files and job state move to external storage.

The Kubernetes deployment therefore uses:

- `replicas: 1`;
- `strategy.type: Recreate`;
- a 10 GiB disk-backed `emptyDir` mounted at `/tmp`;
- `APP_TEMP_ROOT=/tmp/ifctoolkit`.

## Inspect production state

```bash
kubectl get pods -l app=ifctoolkit -o wide
kubectl describe pod -l app=ifctoolkit
kubectl logs deploy/ifctoolkit --tail=200
kubectl logs deploy/ifctoolkit --previous --tail=200
kubectl top pod -l app=ifctoolkit
kubectl exec deploy/ifctoolkit -- df -h /tmp
kubectl exec deploy/ifctoolkit -- du -sh /tmp/ifctoolkit || true
kubectl get events --sort-by=.lastTimestamp | tail -50
kubectl rollout history deployment/ifctoolkit
kubectl rollout status deployment/ifctoolkit --timeout=10m
```

## Health and smoke checks

```bash
curl --fail --silent --show-error https://ifctoolkit.com/health/live
curl --fail --silent --show-error https://ifctoolkit.com/health/ready
curl --fail --silent --show-error https://ifctoolkit.com/health/build-info
curl --fail --silent --show-error https://ifctoolkit.com/ >/dev/null
```

## Traefik inspection

Do not add nginx annotations to this cluster. Inspect Traefik before changing upload timeout or buffering behaviour:

```bash
kubectl get pods -A | grep -i traefik
kubectl logs -n kube-system deploy/traefik --tail=200
kubectl describe ingress ifctoolkit-ingress
```

## Session behaviour

- Container restart in the same pod: `/tmp` survives, so session directories can survive until TTL cleanup removes them.
- Pod replacement or deployment: `emptyDir` is replaced; active sessions and in-memory jobs are lost.
- Node failure: local `emptyDir` data is lost with the pod.
- Future object-storage migration boundary: moving session files, job outputs and job registry state outside the pod is required before increasing replicas or using rolling updates.
