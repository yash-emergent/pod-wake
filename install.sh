#!/usr/bin/env bash
# Installs pod-wake for the current user (macOS / Linux):
#   1. registers the pod-wake MCP server in Claude Code (user scope)
#   2. merges the PreToolUse hook into ~/.claude/settings.json (backup kept, re-run safe)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"
MATCHER='mcp__envcore-gateway__execute_tool|mcp__envcore-gateway__execute_playwright_script|mcp__envcore-gateway__browser_execute_code'

command -v claude >/dev/null || { echo "claude CLI not found on PATH"; exit 1; }
UV="$(command -v uv || true)"; [ -n "$UV" ] || { echo "uv not found on PATH (https://docs.astral.sh/uv/)"; exit 1; }
PY="$(command -v python3 || true)"; [ -n "$PY" ] || { echo "python3 not found on PATH"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "python3 >= 3.9 required (found $("$PY" --version))"; exit 1; }

chmod +x "$HERE"/*.sh "$HERE"/*.py

echo "Registering MCP server pod-wake ..."
claude mcp remove pod-wake -s user >/dev/null 2>&1 || true
claude mcp add --scope user pod-wake -- "$UV" run --script "$HERE/pod_wake_mcp.py" >/dev/null
claude mcp get pod-wake | grep -E 'Status' || true

echo "Merging hook into $SETTINGS ..."
mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || printf '{}\n' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak-podwake-$(date +%Y%m%d%H%M%S)"
HOOK_SH="$HERE/pretool_hook.sh" MATCHER="$MATCHER" "$PY" - "$SETTINGS" <<'PYEOF'
import json, os, shlex, sys, tempfile
path = sys.argv[1]
with open(path, encoding="utf-8-sig") as f:
    data = json.load(f)
hooks = data.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])
# remove only earlier pod-wake hook items (matched on the script name), keep everything else
for group in pre:
    group["hooks"] = [h for h in group.get("hooks", []) if "pretool_hook" not in (h.get("command") or "")]
pre[:] = [g for g in pre if g.get("hooks")]
pre.append({"matcher": os.environ["MATCHER"],
            "hooks": [{"type": "command", "command": shlex.quote(os.environ["HOOK_SH"]), "timeout": 180,
                       "statusMessage": "pod-wake: checking preview pod is awake"}]})
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".settings-", suffix=".json")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2); f.write("\n")
os.replace(tmp, path)
print("hook installed")
PYEOF

echo
echo "Done. Start a NEW Claude Code session, then run /mcp: pod-wake should be listed."
echo "Quick check:  $HERE/podwake.py status <slug>"
