# pod-wake: keep Emergent preview pods awake for Claude Code

Preview pods are deleted by a cron about 30 min after the customer's agent stops. Support edits through
envcore do not count as activity, so a pod you are editing can vanish mid-ticket. The envcore MCP
gateway cannot wake pods (`failed to resolve env_key "...": empty IP`). This tool can.

## What is in here
- `podwake.py` CLI + library (stdlib only). `status <slug>`, `wake <slug>`, `ensure <slug>`.
- `pod_wake_mcp.py` MCP server with `pod_status(env_key)` and `wake_pod(env_key, timeout_sec)`.
- `pretool_hook.sh` / `pretool_hook.py` Claude Code PreToolUse hook for envcore-gateway execute and
  browser tools: reads `env_key` from the tool input, wakes the pod if asleep, then lets the call proceed.
  Silent when awake. Never blocks a call.

`env_key` = app slug (the part before `.preview.emergentagent.com`), pod name, or the preview URL.

## How it wakes
`GET http://envcore.int.apis.emergentagent.com/api/v2/env/proxy/health?env_key=<slug>&port=8010`
runs envcore's EnvEnsurer (restart via ensure-environment, wait for the new IP, health check on 8010).
Typically 30 to 90 s. Needs the Emergent VPN.

## Install (5 minutes)
Requirements: Claude Code, `uv`, Python 3.10+, Emergent VPN.

```bash
git clone https://github.com/yash-emergent/pod-wake.git ~/mcp-servers/pod-wake
chmod +x ~/mcp-servers/pod-wake/*.sh ~/mcp-servers/pod-wake/*.py
claude mcp add --scope user pod-wake -- "$(which uv)" run --script ~/mcp-servers/pod-wake/pod_wake_mcp.py
claude mcp get pod-wake   # should say Connected
```

Add the hook to `~/.claude/settings.json` (merge into an existing `hooks` block; use your real home path):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__envcore-gateway__execute_tool|mcp__envcore-gateway__execute_playwright_script|mcp__envcore-gateway__browser_execute_code",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/<you>/mcp-servers/pod-wake/pretool_hook.sh",
            "timeout": 180,
            "statusMessage": "pod-wake: checking preview pod is awake"
          }
        ]
      }
    ]
  }
}
```

Start a new Claude Code session (servers and hooks load at session start). `/mcp` should list `pod-wake`.

Optional: add to your `~/.claude/CLAUDE.md`: "If a preview pod is asleep or envcore-gateway returns
empty IP, run pod-wake `wake_pod` instead of asking me to wake it in the UI."

## Test
1. `pod_status <slug>` on a running test app: `state: running` with a `pod_ip`.
2. `wake_pod <slug>` on a test app asleep for 30+ min: `action: woken`, new `pod_ip`, 30 to 90 s.
3. With the pod asleep, run any envcore-gateway `execute_bash` on it: it succeeds first try and the
   hook adds a one-line note that the pod was woken.

## Known limits
- VPN only. Off VPN: `unreachable`.
- envcore restart cooldown: 10 min per env. A second wake inside the window may time out; wait and retry.
- Pods asleep for 90+ min are archived and restore slower: pass `timeout_sec: 300` or wake twice.
- Backup failures cannot be fixed by waking; use the UI or escalate.
- Agent-only jobs (wingman, moltbot, brainstorm) have no pod.
- Each wake runs the pod for at least 30 min of compute. The hook fires only on real commands.
- Only restores pods. Never reads, edits or deletes app files or data.
