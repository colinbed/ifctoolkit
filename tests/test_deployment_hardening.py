from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_kubernetes_deployment_local_storage_safety_controls():
    deployment = (ROOT / "k8s" / "deployment.yaml").read_text()

    assert "replicas: 1" in deployment
    assert "type: Recreate" in deployment
    assert "emptyDir:" in deployment
    assert "sizeLimit: 10Gi" in deployment
    assert "mountPath: /tmp" in deployment
    assert "startupProbe:" in deployment
    assert "readinessProbe:" in deployment
    assert "livenessProbe:" in deployment
    assert "path: /health/live" in deployment
    assert "path: /health/ready" in deployment
    assert "terminationGracePeriodSeconds:" in deployment
    assert "runAsNonRoot: true" in deployment
    assert "allowPrivilegeEscalation: false" in deployment
    assert "drop:" in deployment and "ALL" in deployment
    assert "automountServiceAccountToken: false" in deployment


def test_dockerfile_runs_as_non_root():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "useradd" in dockerfile
    assert "USER 10001:10001" in dockerfile


def test_dockerignore_excludes_secret_and_local_files():
    dockerignore = (ROOT / ".dockerignore").read_text()

    for pattern in [".git", ".github", "*kubeconfig*", ".env", ".env.*", "*.pem", "*.key", "tests/"]:
        assert pattern in dockerignore


def test_ci_deploys_immutable_image_and_checks_kube_secret():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()

    assert "IMAGE_TAG: ${{ github.sha }}" in workflow
    assert "docker push \"$IMAGE_REPO:$IMAGE_TAG\"" in workflow
    assert "kubectl set image deployment/ifctoolkit ifctoolkit=\"$IMAGE_REPO:$IMAGE_TAG\"" in workflow
    assert "kubectl rollout restart" not in workflow
    assert "KUBE_CONFIG GitHub secret is not configured" in workflow
