import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_railway_uses_dockerfile_and_live_healthcheck():
    config = json.loads((ROOT / "railway.json").read_text())
    assert config["build"] == {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"}
    assert config["deploy"]["healthcheckPath"] == "/health/live"
    assert config["deploy"]["restartPolicyType"] == "ON_FAILURE"


def test_dockerfile_runs_as_non_root_and_uses_railway_port():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "useradd" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "--host 0.0.0.0 --port ${PORT:-8000}" in dockerfile
    assert "APP_TEMP_ROOT=/tmp/ifctoolkit" in dockerfile


def test_dockerignore_excludes_secret_and_local_files():
    dockerignore = (ROOT / ".dockerignore").read_text()
    for pattern in [".git", ".github", "*kubeconfig*", ".env", ".env.*", "*.pem", "*.key", "tests/"]:
        assert pattern in dockerignore


def test_github_workflow_is_ci_only_and_has_no_legacy_deployment_access():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    assert "name: CI" in workflow
    assert "actions/setup-python@v5" in workflow
    assert "python -m pip install -r requirements.txt" in workflow
    assert "python -m pytest" in workflow
    assert "scripts/check_tracked_secrets.py" in workflow
    for obsolete in ["KUBE_CONFIG", "kubectl", "ghcr.io", "docker push", "packages: write"]:
        assert obsolete not in workflow


def test_kubernetes_manifests_are_legacy_only():
    assert not (ROOT / "k8s").exists()
    runbook = (ROOT / "docs" / "runbooks" / "legacy-civo-deployment.md").read_text()
    assert "Deprecated legacy infrastructure" in runbook
    assert "Railway is the primary deployment platform" in runbook
