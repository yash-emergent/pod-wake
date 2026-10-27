#!/usr/bin/env python3
"""Claude Code PreToolUse hook: auto-wake a sleeping preview pod before any envcore-gateway tool call.
Non-blocking by design: on any failure the tool call still proceeds and Claude gets a context note."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def emit(msg: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}))


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    tool = data.get("tool_name", "")
    if not tool.startswith("mcp__envcore-gateway__"):
        return 0
    env_key = (data.get("tool_input") or {}).get("env_key")
    if not env_key:
        return 0
    try:
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "podwake.py"), "ensure", str(env_key), "--timeout", "150"],
            capture_output=True, text=True, timeout=170,
        )
        r = json.loads(p.stdout or "{}")
    except Exception as e:  # never block the call
        emit(f"pod-wake hook: could not check pod {env_key}: {e}")
        return 0
    action = r.get("action")
    if action == "already_awake":
        return 0  # quiet path
    if action == "woken":
        emit(f"pod-wake hook: preview pod '{env_key}' was asleep and has been woken "
             f"(pod_ip {r.get('pod_ip')}, {r.get('seconds')}s). Proceeding.")
    else:
        emit(f"pod-wake hook: could not wake pod '{env_key}' ({action}: {r.get('error')}). {r.get('hint') or ''} "
             f"If the tool call fails with 'empty IP', call pod-wake wake_pod or ask the user to wake it in the UI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
