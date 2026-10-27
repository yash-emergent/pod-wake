#!/bin/zsh
# Wrapper so the hook works from GUI-launched VS Code (PATH) and never blocks on error.
export PATH="$HOME/.local/bin:/usr/bin:/bin:$PATH"
exec "$HOME/.local/bin/python3" "$HOME/mcp-servers/pod-wake/pretool_hook.py"
