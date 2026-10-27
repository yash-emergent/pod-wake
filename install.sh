#!/usr/bin/env bash
# Installs pod-wake for the current user:
#   1. registers the pod-wake MCP server in Claude Code (user scope)
#   2. merges the PreToolUse hook into ~/.claude/settings.json (backup kept)
# Safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"
MATCHER='mcp__envcore-gateway__execute_tool|mcp__envcore-gateway__execute_playwright_script|mcp__envcore-gateway__browser_execute_code'

command -v claude >/dev/null || { echo "claude CLI not found on PATH"; exit 1; }
UV="$(command -v uv || true)"; [ -n "$UV" ] || { echo "uv not found on PATH (https://docs.astral.sh/uv/)"; exit 1; }
PY="$(command -v python3)"; "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "python3 >= 3.10 required"; exit 1; }

chmod +x "$HERE"/*.sh "$HERE"/*.py

echo "Registering MCP server pod-wake ..."
claude mcp remove pod-wake -s user >/dev/null 2>&1 || true
claude mcp add --scope user pod-wake -- "$UV" run --script "$HERE/pod_wake_mcp.py" >/dev/null
claude mcp get pod-wake | grep -E 'Status' || true

echo "Merging hook into $SETTINGS ..."
mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak-podwake-$(date +%Y%m%d%H%M%S)"
HOOK_CMD="$HERE/pretool_hook.sh" MATCHER="$MATCHER" "$PY" - "$SETTINGS" <<'PYEOF'
import json, os, sys
path = sys.argv[1]
data = json.load(open(path))
hooks = data.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])
entry = {"matcher": os.environ["MATCHER"],
         "hooks": [{"type": "command", "command": os.environ["HOOK_CMD"], "timeout": 180,
                    "statusMessage": "pod-wake: checking preview pod is awake"}]}
pre[:] = [h for h in pre if not any("pod-wake" in (hh.get("command") or "") for hh in h.get("hooks", []))]
pre.append(entry)
json.dump(data, open(path, "w"), indent=2); open(path, "a").write("\n")
print("hook installed")
PYEOF

echo
echo "Done. Start a NEW Claude Code session, then run /mcp: pod-wake should be listed."
echo "Quick check:  $HERE/podwake.py status <slug>"
