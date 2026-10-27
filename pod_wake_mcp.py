# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.6,<2"]
# ///
"""pod-wake MCP: let Claude check and wake Emergent preview pods (envcore EnvEnsurer path).

Tools:
  pod_status(env_key)              read-only
  wake_pod(env_key, timeout_sec)   idempotent wake, blocks until healthy or timeout
Register: claude mcp add --scope user pod-wake -- uv run --script ~/mcp-servers/pod-wake/pod_wake_mcp.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import podwake  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP(
    "pod-wake",
    instructions=(
        "Wake sleeping Emergent preview pods. Call pod_status first when a pod looks asleep "
        "(envcore-gateway returns 'failed to resolve env_key ...: empty IP'), then wake_pod. "
        "env_key is the app slug (the part before .preview.emergentagent.com), a pod name, or the preview URL. "
        "Preview pods sleep ~30 min after the customer's agent stops; support edits via envcore do not count as activity, "
        "so re-check before long edit sessions."
    ),
)


@mcp.tool()
async def pod_status(env_key: str) -> dict:
    """Read-only: is the preview pod for this slug / pod name / preview URL running or asleep? Returns pod_ip, image, updated_at."""
    return await asyncio.to_thread(podwake.status, env_key)


@mcp.tool()
async def wake_pod(env_key: str, timeout_sec: int = 180) -> dict:
    """Wake a sleeping preview pod and wait until it is healthy (typically 30-90 s). Idempotent: returns action already_awake if it is already up (that is success, do not call again).
    Changes state on the customer's environment (restores the pod); use only when you need to work on it."""
    timeout_sec = max(30, min(int(timeout_sec), 600))
    return await asyncio.to_thread(podwake.wake, env_key, timeout_sec, False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
