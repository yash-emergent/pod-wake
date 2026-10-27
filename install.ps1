# Installs pod-wake for the current user on Windows:
#   1. registers the pod-wake MCP server in Claude Code (user scope)
#   2. merges the PreToolUse hook into %USERPROFILE%\.claude\settings.json (backup kept, re-run safe)
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Settings = Join-Path $HOME ".claude\settings.json"
$Matcher = "mcp__envcore-gateway__execute_tool|mcp__envcore-gateway__execute_playwright_script|mcp__envcore-gateway__browser_execute_code"

function Need($name) { $c = Get-Command $name -ErrorAction SilentlyContinue; if (-not $c) { throw "$name not found on PATH" }; return $c.Source }
$null = Need "claude"
$Uv = Need "uv"
$Py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Py) { $Py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $Py) { throw "Python 3.9+ not found on PATH (python or py)" }
$ver = & $Py -c "import sys; print(1 if sys.version_info >= (3, 9) else 0)"
if ("$ver".Trim() -ne "1") { throw "Python 3.9 or newer is required" }

Write-Host "Registering MCP server pod-wake ..."
# Native commands: do not let a non-zero exit or stderr line abort the script (PS 5.1 and 7.4+ differ here).
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
try { & claude mcp remove pod-wake -s user *> $null } catch { }
& claude mcp add --scope user pod-wake -- $Uv run --script (Join-Path $Here "pod_wake_mcp.py") *> $null
& claude mcp get pod-wake 2>&1 | Select-String "Status"
$ErrorActionPreference = $prev

Write-Host "Merging hook into $Settings ..."
New-Item -ItemType Directory -Force -Path (Split-Path $Settings) | Out-Null
if (-not (Test-Path $Settings)) { [IO.File]::WriteAllText($Settings, "{}`n") }   # no BOM
Copy-Item $Settings "$Settings.bak-podwake-$(Get-Date -Format yyyyMMddHHmmss)"

$env:POD_WAKE_PY = $Py
$env:POD_WAKE_HOOK = (Join-Path $Here "pretool_hook.py")
$env:POD_WAKE_MATCHER = $Matcher
$MergeCode = @'
import json, os, sys, tempfile
path = sys.argv[1]
with open(path, encoding="utf-8-sig") as f:      # tolerate a BOM written by older PowerShell
    data = json.load(f)
hooks = data.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])
def is_ours(h):
    return "pretool_hook" in (h.get("command") or "") or any("pretool_hook" in a for a in (h.get("args") or []))
for group in pre:
    group["hooks"] = [h for h in group.get("hooks", []) if not is_ours(h)]
pre[:] = [g for g in pre if g.get("hooks")]
# exec form (command + args, no shell) so it works on Windows without Git Bash
pre.append({"matcher": os.environ["POD_WAKE_MATCHER"],
            "hooks": [{"type": "command", "command": os.environ["POD_WAKE_PY"], "args": [os.environ["POD_WAKE_HOOK"]],
                       "timeout": 180, "statusMessage": "pod-wake: checking preview pod is awake"}]})
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".settings-", suffix=".json")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2); f.write("\n")
os.replace(tmp, path)
print("hook installed")
'@
$MergeCode | & $Py - $Settings

Write-Host ""
Write-Host "Done. Start a NEW Claude Code session, then run /mcp: pod-wake should be listed."
Write-Host "Quick check:  python `"$Here\podwake.py`" status <slug>"
