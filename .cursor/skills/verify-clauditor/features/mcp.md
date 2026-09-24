# MCP tools

A reviewer registers `clauditor mcp` with Claude Code and asks questions in chat. The MCP server exposes `audit_sessions` and `list_roles` over newline-delimited JSON-RPC on stdio. In local mode it audits a projects root on the reviewer's machine. In remote mode it calls the server's HTTP API with the read token.

## Sub-features

- `mcp-handshake` answers `initialize` with `serverInfo.name` `clauditor` and echoes a supported `protocolVersion`.
- `mcp-list` returns exactly two tools from `tools/list`: `audit_sessions` and `list_roles`.
- `mcp-audit-local` makes `audit_sessions` audit `--root`, with `format` defaulting to `markdown`.
- `mcp-audit-remote` makes `audit_sessions` proxy `/v1/audit` when `--server` or `CLAUDITOR_SERVER` is set.
- `mcp-roles` makes `list_roles` return the same JSON as `/v1/roles`.
- `mcp-errors` reports tool failures (the `user` filter in local mode, a bad token, an unreachable server) as `isError: true` with the reason as text. The process does not crash.

## How to get to it (user POV)

- `claude mcp add clauditor -e PYTHONPATH=<clone> [-e CLAUDITOR_SERVER=<url> -e CLAUDITOR_READ_TOKEN=<token>] -- python3 -m clauditor mcp`, then ask Claude Code to audit.
- Run `python3 -m clauditor mcp --root <dir>` or `python3 -m clauditor mcp --server <url>` and write JSON-RPC to its stdin.

## Driving it with verify.sh

Preconditions:

- `RUN=<id> $V setup` has run.
- For remote steps, `RUN=<id> $V serve` has run, and both seed sessions are ingested with `RUN=<id> $V capture mcp/seed 'python3 -m clauditor collect --server "$V_URL" --root "$V_ROOT" --state-file "$V_STATE"'`.
- `"$V_HELPER" rpc <tool> key=value...` writes the `initialize`, `tools/list`, and `tools/call` lines. Replies come back as one JSON object per line, with `id` 1, 2, and 3.

- **Local audit.** Run `RUN=<id> $V capture mcp/local '"$V_HELPER" rpc audit_sessions format=text show_aligned=true | python3 -m clauditor mcp --root "$V_ROOT"'`. Exit is `0`. Reply 1 has `"serverInfo": {"name": "clauditor", "version": "0.1.0"}`. Reply 2 lists `audit_sessions` and `list_roles`. Reply 3 has `"isError": false`, and its text contains `FORBIDDEN network-scanning (high)  shell  nmap -p 5432 10.0.0.0/24` and `2 sessions: 1 drifted, 0 review, 1 aligned, 0 unassigned`.
- **Default markdown.** Run `RUN=<id> $V capture mcp/local-md '"$V_HELPER" rpc audit_sessions | python3 -m clauditor mcp --root "$V_ROOT"'`. Reply 3's text starts with `# clauditor report`.
- **Local user filter is refused.** Run `RUN=<id> $V capture mcp/local-user '"$V_HELPER" rpc audit_sessions user=x | python3 -m clauditor mcp --root "$V_ROOT"'`. Reply 3 is `"isError": true` with text `user filter needs the central server, local transcripts have one operator`.
- **Remote audit.** Run `RUN=<id> $V capture mcp/remote '"$V_HELPER" rpc audit_sessions format=text user=$(id -un) | python3 -m clauditor mcp --server "$V_URL"'`. Reply 3 is `"isError": false`, and its text shows the same `DRIFTED    drift 20  software-engineer    drift_se` block as `scan`.
- **Remote roles.** Run `RUN=<id> $V capture mcp/roles '"$V_HELPER" rpc list_roles | python3 -m clauditor mcp --server "$V_URL"'`. Reply 3's text is the `/v1/roles` JSON, starting `{"default_role": "software-engineer"`.
- **Bad token.** Run `RUN=<id> $V capture mcp/bad-token '"$V_HELPER" rpc list_roles | CLAUDITOR_READ_TOKEN=bad python3 -m clauditor mcp --server "$V_URL"'`. Reply 3 is `"isError": true` with text `401 unauthorized`.
- **Proof.** Keep `mcp/local` and `mcp/remote`. Together they show both modes return the same findings for the same transcripts.

## Gotchas

- The MCP server exits when stdin closes, so each capture is one complete session. The `rpc` helper always sends `initialize` first. Real clients do the same.
- If `CLAUDITOR_SERVER` is set, `mcp` goes remote even without `--server`. `capture` unsets it, but a shell where you ran `mcp` by hand might not.
- Notifications (messages without an `id`) get no reply. Count replies by `id`, not by lines sent.
- A tool error is a successful JSON-RPC result with `isError: true`, not a JSON-RPC `error`. Assert on `result.isError`.
- Local mode reads the policy at startup from `--policy-dir`, which defaults to the repo's `policy/`. Remote mode uses whatever policy the server loaded.
