#!/bin/sh
# Claude Code PreToolUse hook wrapper (macOS/Linux). Resolves its own directory and a python3,
# so it works wherever the repo is cloned and from GUI-launched editors with a minimal PATH.
# Never blocks a tool call: any failure exits 0.
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
for PY in "$POD_WAKE_PYTHON" python3 python; do
  [ -n "$PY" ] && command -v "$PY" >/dev/null 2>&1 && exec "$PY" "$HERE/pretool_hook.py"
done
exit 0
