"""sdlc_orchestrator/tools/deploy_tools.py
Helm + kubectl deployment helpers.
"""
import os
import asyncio
import json

KUBECONFIG  = os.environ.get("KUBECONFIG", os.path.expanduser("~/.kube/config"))
HELM_CHART  = os.environ.get("HELM_CHART_PATH", "../aisdlc-infra/helm/aisdlc-orchestrator")
HELM_NS     = os.environ.get("HELM_NAMESPACE", "aisdlc")


async def _run(cmd: list[str], timeout: int = 120) -> dict:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "KUBECONFIG": KUBECONFIG},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"success": False, "error": "timeout", "stdout": "", "stderr": ""}
    return {
        "success":  proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout":   stdout.decode(),
        "stderr":   stderr.decode(),
    }


async def helm_upgrade(
    release: str,
    values_file: str,
    image_tag: str,
    namespace: str = HELM_NS,
) -> dict:
    """Run helm upgrade --install with override values."""
    cmd = [
        "helm", "upgrade", "--install", release, HELM_CHART,
        "--namespace",  namespace,
        "--create-namespace",
        "--values",     values_file,
        "--set",        f"image.tag={image_tag}",
        "--wait",
        "--timeout",    "5m",
    ]
    return await _run(cmd, timeout=360)


async def helm_rollback(release: str, revision: int = 0, namespace: str = HELM_NS) -> dict:
    """Roll back a Helm release. revision=0 rolls back to previous."""
    cmd = ["helm", "rollback", release, str(revision), "--namespace", namespace, "--wait"]
    return await _run(cmd, timeout=180)


async def get_pod_status(namespace: str = HELM_NS) -> list[dict]:
    """Return pod statuses in the given namespace as a list of dicts."""
    result = await _run(
        ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
        timeout=30,
    )
    if not result["success"]:
        return []
    items = json.loads(result["stdout"]).get("items", [])
    return [
        {
            "name":   p["metadata"]["name"],
            "phase":  p["status"].get("phase", "Unknown"),
            "ready":  all(
                c.get("ready", False)
                for c in p["status"].get("containerStatuses", [])
            ),
        }
        for p in items
    ]


async def wait_for_rollout(
    deployment: str, namespace: str = HELM_NS, timeout_seconds: int = 180
) -> dict:
    cmd = [
        "kubectl", "rollout", "status",
        f"deployment/{deployment}",
        "-n", namespace,
        f"--timeout={timeout_seconds}s",
    ]
    return await _run(cmd, timeout=timeout_seconds + 10)


async def apply_manifests(manifest_path: str) -> dict:
    cmd = ["kubectl", "apply", "-f", manifest_path]
    return await _run(cmd, timeout=60)
