# Paste this into Claude once after installing

Copy everything in the box below and send it to Claude Code in a new session. It tells Claude to use pod-wake on its own from then on, so you never have to say "wake the pod" again.

```
You now have a tool called pod-wake. It gives you two MCP tools: pod_status(env_key) and wake_pod(env_key).

Context: Emergent preview pods go to sleep about 30 minutes after the customer's agent stops. When a pod is asleep, every envcore-gateway call fails with: failed to resolve env_key "<slug>": empty IP. The env_key is the app slug (the part before .preview.emergentagent.com), the pod name, or the preview URL.

From now on, whenever a preview pod is asleep, or an envcore-gateway call fails with "empty IP", do not ask me to wake it from the Emergent UI. Instead:
1. Run pod_status on the slug to confirm it is asleep.
2. Run wake_pod on the slug and wait for it to report action: woken or already_awake (usually 30 to 90 seconds). Both mean the pod is up; do not call it again.
3. Continue exactly what you were doing before.

Also, when I give you a ticket or a slug and ask you to investigate or change something on the preview pod, check pod_status first and wake it if needed before running commands.

If wake_pod fails, times out, or returns not_found or unreachable, show me the exact output and stop. Do not call it again; envcore has a 10 minute cooldown between restarts.

Please save this as a permanent memory so it applies in every future session.
```

Prefer a one-liner? Add this to your `~/.claude/CLAUDE.md` instead:

> If a preview pod is asleep or envcore-gateway returns "empty IP", run pod-wake `wake_pod` on the slug and continue. Do not ask me to wake it in the UI.
