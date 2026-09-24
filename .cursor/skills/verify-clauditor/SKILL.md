---
name: verify-clauditor
description: Drive the real clauditor app to prove behavior end to end. Covers the `python3 -m clauditor` CLI (scan, demo, collect), the HTTP audit server (serve, /v1/ingest, /v1/audit, /v1/roles), and the stdio MCP server (audit_sessions, list_roles). Use when you change clauditor and need evidence that a user-visible path works, beyond unit tests.
---

# Verify clauditor

clauditor is a Python 3.12+ stdlib CLI with no dependencies. There is no build step and no UI. What a user touches:

- **CLI** (primary): `python3 -m clauditor scan | demo | collect | serve | mcp`, run from the repo root.
- **HTTP server**: `serve` stores collected sessions in SQLite and audits them on request, behind two bearer tokens.
- **MCP server**: `mcp` speaks newline-delimited JSON-RPC on stdio, locally (`--root`) or against a server (`--server`).

The harness is `verify.sh` in this directory. It gives each run its own projects root, SQLite file, collector state file, tokens, and server port, so parallel runs never share state. All commands take the run id in `RUN`.

```
V=.cursor/skills/verify-clauditor/verify.sh
```

Every example below runs from the repo root and prefixes `RUN=<id>`. Use a fresh id per verification, like `RUN=scan-$(date +%s)`.

## Launch

1. `RUN=r1 $V setup` builds the disposable run under `~/.cache/clauditor-verify/r1/` (override the home with `CLAUDITOR_VERIFY_HOME`). It seeds `scratch/projects/-Users-dev-code-acme-web/` with two sessions:
   - `coding_session` is the repo fixture, with one subagent. It is aligned for `software-engineer` and drifted for `support-analyst`.
   - `drift_session` comes from `seed/drift_session.jsonl`. It runs `nmap` and `hydra`, so it is drifted for `software-engineer` (drift 20).
2. For server or collector work, `RUN=r1 $V serve` starts `clauditor serve` on `127.0.0.1` with a free port, this run's DB, and two distinct random tokens. It is ready when the helper prints `server for r1 ready at http://127.0.0.1:<port>`. That is the helper finding `clauditor serving on http://127.0.0.1:<port>` in `scratch/server.log`. CLI-only features (`scan`, `demo`, local `mcp`) do not need a server.

Teardown is `RUN=r1 $V cleanup`. See Cleanup.

## Doctor

`RUN=r1 $V doctor` is read-only and exits non-zero on any `FAIL`. Run it first, and again whenever anything looks off. It checks:

- `python3` is 3.12 or newer. It also prints the repo path, commit, and branch under test, so you can confirm you are driving your worktree and not another checkout.
- The run is set up, with 2 transcripts in its projects root.
- If a server was started, the pid is alive and was launched with this run's `--db`, and that pid holds the port.
- `/healthz` returns `ok`.
- `/v1/roles` returns 401 with no token, 401 with the ingest token, and 200 with the read token.

## Drive

Run every command through `capture`. It runs the command with `bash -c` from the repo root, saves the evidence, echoes stdout and stderr, and returns the command's exit code.

```
RUN=r1 $V capture <name> '<shell command>'
```

Keep the command in single quotes so these variables expand inside the run, not in your shell:

| variable | value |
|---|---|
| `$V_ROOT` | this run's projects root. Pass it as `--root` every time. |
| `$V_STATE` | this run's collector state file. Pass it as `--state-file` every time. |
| `$V_DB` | this run's SQLite database |
| `$V_URL` | this run's server URL, set after `serve` |
| `$V_READ_TOKEN`, `$V_INGEST_TOKEN` | this run's tokens. They are also exported as `CLAUDITOR_READ_TOKEN` and `CLAUDITOR_INGEST_TOKEN`. |
| `$V_HELPER` | the absolute path of `verify.sh` |
| `$V_EVIDENCE` | this run's evidence directory |

`capture` unsets any inherited `CLAUDITOR_SERVER`, so a real team server in your environment is never hit. Pass `--server "$V_URL"` explicitly.

Recipes:

```
RUN=r1 $V capture scan 'python3 -m clauditor scan --root "$V_ROOT" --show-aligned'
RUN=r1 $V capture collect 'python3 -m clauditor collect --server "$V_URL" --root "$V_ROOT" --state-file "$V_STATE"'
RUN=r1 $V capture audit 'curl -s -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/audit?format=markdown&show_aligned=1"'
RUN=r1 $V capture rows 'sqlite3 "$V_DB" "select session_id, email, local_user from sessions; select count(*) from activities;"'
RUN=r1 $V capture mcp '"$V_HELPER" rpc audit_sessions format=text show_aligned=true | python3 -m clauditor mcp --root "$V_ROOT"'
```

`verify.sh rpc <tool> [key=value ...]` prints three JSON-RPC lines: `initialize`, `tools/list`, and `tools/call`. The values `true` and `false` become JSON booleans. The per-feature recipes live in `features/`. Read `features/README.md` before driving.

**Never** run `scan`, `collect`, or `mcp` without `--root "$V_ROOT"`. The default is your real `~/.claude/projects`. **Never** run `collect` without `--state-file "$V_STATE"`. The default `~/.clauditor/collector-state.json` belongs to the real collector.

## Evidence

Evidence lives in `~/.cache/clauditor-verify/<RUN>/evidence/` and survives cleanup. Each capture writes:

- `<name>.cmd`: the command, a UTC timestamp, the repo commit, and the run's variables with tokens redacted
- `<name>.out` and `<name>.err`: stdout and stderr
- `<name>.exit`: the exit code

Cleanup also copies `server.log` into evidence.

Proof standards:

- Drive the real user path: the CLI entry, HTTP with the real tokens, and MCP over stdio. Do not call `audit()` or `Store` from Python, and do not start the server with `--no-auth` unless auth is out of scope and you say so.
- Capture the action and the resulting state. For ingest, capture the `collect` output, then the audit the server returns, then the rows in `$V_DB`.
- Check side effects alongside output: DB rows after ingest, the collector state file after `collect`, and exit codes for `--fail-on-drift`.
- Cite findings by the `file:line uuid` the report prints. Those citations are the product.
- Nothing here is mocked. The server is local, and the only outside file read is `~/.claude.json`, where the collector takes the operator email. It is read-only.

## Cleanup

`RUN=r1 $V cleanup` stops the server whose pid is in this run's pidfile, and only if that pid was launched with this run's `--db`. It copies `server.log` to evidence, deletes `scratch/` (projects, DB, state, tokens), and lists the evidence it kept. Never `pkill -f clauditor`, because a user or another run may be serving. Run cleanup after every run, including failed ones. Use `$V list` to find runs that were not cleaned up, and `RUN=<id> $V stop` to stop a server but keep its scratch for inspection.

## Helpers

- `verify.sh` is executable and has these commands: `setup`, `serve`, `doctor`, `env`, `capture <name> '<cmd>'`, `rpc <tool> [k=v...]`, `stop`, `cleanup`, and `list`. Run it with no arguments for usage.
- `seed/drift_session.jsonl` is the seeded drifted transcript that `setup` copies into each run.
