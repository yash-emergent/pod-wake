#!/usr/bin/env python3
"""Wake and inspect Emergent preview pods via envcore (stdlib only).

Mechanism (verified 2026-10-26 in emergentbase/envcore):
  GET /api/v1/env/info?env_key=<slug>              -> pod_ip "" means asleep
  GET /api/v2/env/proxy/<path>?env_key=&port=8010   -> runs EnvEnsurer: restarts the pod
                                                       (ensure-environment), waits for the IP,
                                                       health-checks :8010, then proxies.
The v1 proxy and the MCP gateway (/api/v1/env/gateway) do NOT run the ensurer.

CLI:
  podwake.py status <env_key>
  podwake.py wake   <env_key> [--timeout 180]
  podwake.py ensure <env_key> [--timeout 180]   # wake only if asleep/unhealthy (used by the hook)
env_key may be a slug, a pod name (env id) or a *.preview.emergentagent.com URL.
Needs the Emergent VPN.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("ENVCORE_BASE_URL", "http://envcore.int.apis.emergentagent.com").rstrip("/")
POD_PORT = int(os.environ.get("ENVCORE_POD_PORT", "8010"))
DEFAULT_TIMEOUT = int(os.environ.get("POD_WAKE_TIMEOUT", "180"))
PREVIEW_SUFFIX = ".preview.emergentagent.com"


def normalize_env_key(value: str) -> str:
    v = (value or "").strip()
    if not v:
        raise ValueError("env_key is required (slug, pod name or preview URL)")
    if "://" in v or PREVIEW_SUFFIX in v:
        host = urllib.parse.urlparse(v if "://" in v else "https://" + v).hostname or ""
        if host.endswith(PREVIEW_SUFFIX):
            v = host[: -len(PREVIEW_SUFFIX)]
            v = v.split(".")[-1]  # strip e.g. "internal." prefixes
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", v):
        raise ValueError(f"env_key looks invalid: {value!r}")
    return v


def _get(path: str, params: dict, timeout: float) -> tuple[int, str]:
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "pod-wake/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, f"{type(e).__name__}: {e}"


def status(env_key: str) -> dict:
    """Read-only. Returns pod info plus a derived `state`."""
    key = normalize_env_key(env_key)
    code, body = _get("/api/v1/env/info", {"env_key": key}, 15)
    out = {"env_key": key, "preview_url": f"https://{key}{PREVIEW_SUFFIX}"}
    if code == 0:
        out.update(state="unreachable", error=body, hint="Is the Emergent VPN connected?")
        return out
    if code == 404:
        out.update(state="not_found", http_status=code, error=body[:300])
        return out
    if code != 200:
        out.update(state="error", http_status=code, error=body[:300])
        return out
    try:
        info = json.loads(body)
    except ValueError:
        out.update(state="error", http_status=code, error=body[:300])
        return out
    out.update(
        pod_name=info.get("pod_name"),
        pod_ip=info.get("pod_ip") or "",
        env_image=(info.get("env_image") or "").rsplit("/", 1)[-1],
        updated_at=info.get("updated_at"),
    )
    out["state"] = "asleep" if not out["pod_ip"] else "running"
    return out


def health(env_key: str, timeout: float = 20) -> tuple[bool, str]:
    """Plain health probe through the v1 proxy (no ensurer, no side effects)."""
    code, body = _get("/api/v1/env/proxy/health", {"env_key": env_key, "port": POD_PORT}, timeout)
    return code == 200, f"HTTP {code}: {body[:200]}"


def wake(env_key: str, timeout: int = DEFAULT_TIMEOUT, only_if_needed: bool = False) -> dict:
    """Wake the pod through the v2 proxy (EnvEnsurer). Idempotent."""
    key = normalize_env_key(env_key)
    started = time.time()
    before = status(key)
    result = {"env_key": key, "preview_url": before["preview_url"], "before": before["state"]}
    if before["state"] in ("not_found", "unreachable"):
        result.update(ok=False, action="none", error=before.get("error"), hint=before.get("hint"))
        return result

    if only_if_needed and before["state"] == "running":
        ok, detail = health(key)
        if ok:
            result.update(ok=True, action="already_awake", pod_ip=before["pod_ip"], seconds=round(time.time() - started, 1))
            return result
        result["note"] = f"pod has an IP but health probe failed ({detail}); forcing ensure"

    attempts = 0
    last = ""
    while True:
        remaining = timeout - (time.time() - started)
        if remaining <= 5:
            break
        attempts += 1
        code, body = _get("/api/v2/env/proxy/health", {"env_key": key, "port": POD_PORT}, min(remaining, 150))
        last = f"HTTP {code}: {body[:200]}"
        if code == 200:
            after = status(key)
            result.update(
                ok=True,
                action="already_awake" if before["state"] == "running" else "woken",
                pod_ip=after.get("pod_ip"),
                attempts=attempts,
                seconds=round(time.time() - started, 1),
            )
            return result
        if code == 404:
            result.update(ok=False, action="none", error=last, attempts=attempts)
            return result
        time.sleep(5)

    result.update(
        ok=False,
        action="timeout",
        error=last,
        attempts=attempts,
        seconds=round(time.time() - started, 1),
        hint="envcore restart cooldown is 10 min; if the env was restored recently, wait and retry, "
             "or wake it from the Emergent UI.",
    )
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("status", "wake", "ensure"):
        print(__doc__, file=sys.stderr)
        return 2
    cmd, key = argv[0], argv[1]
    timeout = DEFAULT_TIMEOUT
    if "--timeout" in argv:
        timeout = int(argv[argv.index("--timeout") + 1])
    try:
        if cmd == "status":
            out = status(key)
        else:
            out = wake(key, timeout=timeout, only_if_needed=(cmd == "ensure"))
    except ValueError as e:
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok", out.get("state") in ("running", "asleep")) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
