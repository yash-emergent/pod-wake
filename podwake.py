#!/usr/bin/env python3
"""Check and wake Emergent preview pods via envcore (standard library only).

Mechanism (verified 2026-10-26 in emergentbase/envcore):
  GET /api/v1/env/info?env_key=<slug>               -> pod_ip "" means asleep; 404 means unknown slug
  GET /api/v1/env/proxy/health?env_key=&port=8010   -> plain proxy, no side effects (health probe)
  GET /api/v2/env/proxy/health?env_key=&port=8010   -> runs EnvEnsurer: restarts the pod when it has
                                                       no IP, waits for the IP, health-checks :8010,
                                                       then proxies. Restart-capable: call deliberately.

CLI:
  podwake.py status <env_key>
  podwake.py wake   <env_key> [--timeout 180]   # explicit wake; restores the pod if asleep or unhealthy
  podwake.py ensure <env_key> [--timeout 180]   # hook path: wakes ONLY when the pod has no IP
env_key: app slug, pod name (env id) or a *.preview.emergentagent.com URL. Needs the Emergent VPN.
"""
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("ENVCORE_BASE_URL", "http://envcore.int.apis.emergentagent.com").rstrip("/")
POD_PORT = int(os.environ.get("ENVCORE_POD_PORT", "8010"))
DEFAULT_TIMEOUT = int(os.environ.get("POD_WAKE_TIMEOUT", "180"))
PREVIEW_SUFFIX = ".preview.emergentagent.com"
MAX_ENSURE_ATTEMPTS = 3          # bounded: the ensurer is restart-capable, never hammer it
RETRY_SLEEP = 5
ENSURE_CALL_TIMEOUT = 150        # the ensurer itself polls up to ~120 s


def normalize_env_key(value: str) -> str:
    v = (value or "").strip()
    if not v:
        raise ValueError("env_key is required (slug, pod name or preview URL)")
    if "://" in v or PREVIEW_SUFFIX in v:
        host = urllib.parse.urlparse(v if "://" in v else "https://" + v).hostname or ""
        if host.endswith(PREVIEW_SUFFIX):
            # <slug>.preview... and <slug>.internal.preview...: the slug is the FIRST label
            v = host[: -len(PREVIEW_SUFFIX)].split(".")[0]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", v):
        raise ValueError(f"env_key looks invalid: {value!r}")
    return v


def _get(path: str, params: dict, timeout: float) -> tuple[int, str]:
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "pod-wake/1.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, f"{type(e).__name__}: {e}"


def status(env_key: str) -> dict:
    """Read-only. Returns pod info plus a derived `state`: running | asleep | not_found | unreachable | error."""
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


def health(env_key: str, timeout: float = 15) -> tuple[bool, str]:
    """Plain health probe through the v1 proxy. No side effects, never restarts anything."""
    code, body = _get("/api/v1/env/proxy/health", {"env_key": env_key, "port": POD_PORT}, timeout)
    return code == 200, f"HTTP {code}: {body[:200]}"


class _EnvLock:
    """Per-env advisory lock so parallel hook invocations trigger one restart, not N."""

    def __init__(self, key: str, wait: float):
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
        self.path = os.path.join(tempfile.gettempdir(), f"pod-wake-{safe}.lock")
        self.wait = wait
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "a+")
        deadline = time.time() + self.wait
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() >= deadline:
                    return self  # proceed anyway; envcore's own lock and cooldown still apply
                time.sleep(1)

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_UN)
        except OSError:
            pass
        self.fh.close()


def wake(env_key: str, timeout: int = DEFAULT_TIMEOUT, only_if_needed: bool = False) -> dict:
    """Wake the pod through the v2 proxy (EnvEnsurer).

    only_if_needed=True (hook path): restart ONLY when the pod has no IP. A running pod is never
    touched, even if its health probe fails; the tool call will surface the real error instead.
    only_if_needed=False (explicit wake_pod): a running pod gets a health probe first; the ensurer
    is invoked only if it is asleep or unhealthy.
    """
    key = normalize_env_key(env_key)
    started = time.time()
    timeout = max(int(timeout), 1)
    before = status(key)
    result = {"env_key": key, "preview_url": before["preview_url"], "before": before["state"]}
    if before["state"] != "asleep" and before["state"] != "running":
        result.update(ok=False, action="none", error=before.get("error"), hint=before.get("hint"))
        return result

    if before["state"] == "running":
        if only_if_needed:
            result.update(ok=True, action="already_awake", pod_ip=before["pod_ip"], seconds=round(time.time() - started, 1))
            return result
        ok, detail = health(key)
        if ok:
            result.update(ok=True, action="already_awake", pod_ip=before["pod_ip"], seconds=round(time.time() - started, 1))
            return result
        result["note"] = f"pod has an IP but is not healthy ({detail}); asking envcore to restore it"

    with _EnvLock(key, wait=min(timeout, ENSURE_CALL_TIMEOUT)):
        # Another invocation may have woken it while we waited for the lock.
        now = status(key)
        if now["state"] == "running" and before["state"] == "asleep":
            result.update(ok=True, action="woken", pod_ip=now["pod_ip"], attempts=0, seconds=round(time.time() - started, 1),
                          note="woken by a concurrent invocation")
            return result

        attempts = 0
        last = ""
        while attempts < MAX_ENSURE_ATTEMPTS:
            remaining = timeout - (time.time() - started)
            if attempts > 0 and remaining <= RETRY_SLEEP:
                break
            attempts += 1
            code, body = _get("/api/v2/env/proxy/health", {"env_key": key, "port": POD_PORT},
                              max(5, min(remaining, ENSURE_CALL_TIMEOUT)))
            last = f"HTTP {code}: {body[:200]}"
            if code == 200:
                after = status(key)
                result.update(ok=True, action="already_awake" if before["state"] == "running" else "woken",
                              pod_ip=after.get("pod_ip"), attempts=attempts, seconds=round(time.time() - started, 1))
                return result
            if code == 404:
                result.update(ok=False, action="none", error=last, attempts=attempts)
                return result
            if code not in (0, 408, 429, 500, 502, 503, 504):
                break  # a definite answer from envcore (e.g. 400 cannot restore); do not loop on it
            time.sleep(RETRY_SLEEP)

    result.update(
        ok=False, action="timeout" if (time.time() - started) >= timeout else "failed",
        error=last, attempts=attempts, seconds=round(time.time() - started, 1),
        hint="envcore allows one restart per env every 10 min; if the env was restored recently, wait and retry. "
             "Archived pods (asleep 90+ min) restore slowly: retry with a 300 s timeout. "
             "If it still fails, wake it from the Emergent UI or escalate.",
    )
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] not in ("status", "wake", "ensure"):
        print(__doc__, file=sys.stderr)
        return 2
    cmd, key = argv[0], argv[1]
    try:
        timeout = DEFAULT_TIMEOUT
        if "--timeout" in argv:
            timeout = int(argv[argv.index("--timeout") + 1])
        if cmd == "status":
            out = status(key)
        else:
            out = wake(key, timeout=timeout, only_if_needed=(cmd == "ensure"))
    except (ValueError, IndexError) as e:
        out = {"ok": False, "error": f"bad arguments: {e}"}
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok", out.get("state") in ("running", "asleep")) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
