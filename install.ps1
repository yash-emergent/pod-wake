# Installs pod-wake for the current user on Windows:
#   1. registers the pod-wake MCP server in Claude Code (user scope)
#   2. merges the PreToolUse hook into %USERPROFILE%\.claude\settings.json (backup kept)
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File .\install.ps1
# Safe to re-run.
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Settings = Join-Path $HOME ".claude\settings.json"
$Matcher = "mcp__envcore-gateway__execute_tool|mcp__envcore-gateway__execute_playwright_script|mcp__envcore-gateway__browser_execute_code"

function Need($name) { $c = Get-Command $name -ErrorAction SilentlyContinue; if (-not $c) { throw "$name not found on PATH" }; return $c.Source }
$Claude = Need "claude"
$Uv = Need "uv"
$Py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Py) { $Py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $Py) { throw "Python 3.10+ not found on PATH (python or py)" }
$ver = & $Py -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)"
if ($ver -ne "1") { throw "Python 3.10 or newer is required" }

Write-Host "Registering MCP server pod-wake ..."
& claude mcp remove pod-wake -s user 2>$null | Out-Null
& claude mcp add --scope user pod-wake -- $Uv run --script (Join-Path $Here "pod_wake_mcp.py") | Out-Null
& claude mcp get pod-wake | Select-String "Status"

Write-Host "Merging hook into $Settings ..."
New-Item -ItemType Directory -Force -Path (Split-Path $Settings) | Out-Null
if (-not (Test-Path $Settings)) { Set-Content -Path $Settings -Value "{}" -Encoding UTF8 }
Copy-Item $Settings "$Settings.bak-podwake-$(Get-Date -Format yyyyMMddHHmmss)"

$HookPy = Join-Path $Here "pretool_hook.py"
$env:POD_WAKE_PY = $Py; $env:POD_WAKE_HOOK = $HookPy; $env:POD_WAKE_MATCHER = $Matcher
& $Py - $Settings @'
import json, os, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
hooks = data.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])
# exec form (command + args, no shell) so it works on Windows without Git Bash
entry = {"matcher": os.environ["POD_WAKE_MATCHER"],
         "hooks": [{"type": "command", "command": os.environ["POD_WAKE_PY"], "args": [os.environ["POD_WAKE_HOOK"]],
                    "timeout": 180, "statusMessage": "pod-wake: checking preview pod is awake"}]}
pre[:] = [h for h in pre if not any("pod-wake" in (hh.get("command") or "") or any("pod-wake" in a for a in (hh.get("args") or []))
                                     for hh in h.get("hooks", []))]
pre.append(entry)
json.dump(data, open(path, "w", encoding="utf-8"), indent=2)
print("hook installed")
'@

Write-Host ""
Write-Host "Done. Start a NEW Claude Code session, then run /mcp: pod-wake should be listed."
Write-Host "Quick check:  python `"$Here\podwake.py`" status <slug>"
