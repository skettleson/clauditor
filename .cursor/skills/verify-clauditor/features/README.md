# clauditor verification map

This directory is the maintained source for verifying the user-facing behavior of clauditor. Read this index before driving the app, then use the matching feature file as the recipe. The harness is `verify.sh` in the parent directory. `../SKILL.md` covers launch, doctor, and cleanup.

## Baseline preconditions

- Run everything from the repo root, with `V=.cursor/skills/verify-clauditor/verify.sh`.
- `RUN=<id> $V setup` has created a fresh run. Its projects root holds exactly `coding_session` (aligned for `software-engineer`, drift 0) and `drift_session` (drifted for `software-engineer`, drift 20, `nmap` and `hydra`).
- The policy is the repo's own `policy/`. If the change under test edits the policy, expect the verdicts below to change, and say so in the report.
- For server features, `RUN=<id> $V serve` has run, and `RUN=<id> $V doctor` reports no `FAIL`.
- Never drive a server this run did not start. `$V_URL` is always this run's server.

## Driving conventions

- Run every step as `RUN=<id> $V capture <feature>/<step> '<command>'`, so evidence lands in `evidence/<feature>/`.
- Pass `--root "$V_ROOT"` to `scan`, `collect`, and local `mcp`. Pass `--state-file "$V_STATE"` to `collect`. The defaults point at the operator's real data.
- Prefer `--format json`, or `format=json` on the server, for assertions. Text output truncates session ids to 8 characters (`drift_se`).
- Start each feature from the baseline. `collect` mutates the run's DB and state file, so a feature that assumes an empty DB needs a new `RUN`.

## Proof and skip reporting

- CLI proof is the `.cmd`, `.out`, `.err`, and `.exit` files for the step.
- A mutation needs a second, read-only view. After ingest, capture both the server audit and `sqlite3 "$V_DB"` rows.
- A finding counts as proven only when the output cites `file:line uuid` and that line in the run's transcript really holds the cited command.
- Record which entry point each artifact used (CLI, HTTP, MCP-local, MCP-remote).
- If an entry point was unreachable, report the attempted command and the unmet precondition. Do not report it as verified through another path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph on the user-visible behavior. Four H2 sections follow, in this order: `Sub-features`, `How to get to it (user POV)`, `Driving it with verify.sh`, and `Gotchas`. The driving section starts with `Preconditions:`. Each bullet after that pairs a user action with an exact command and the result you should observe.

## Features

- [Scan local transcripts](./scan.md) covers `scan` and `demo`: verdicts, citations, role override, session filter, formats, and the `--fail-on-drift` exit code.
- [Collect and ingest](./collect.md) covers the collector shipping transcripts to the server, incremental re-runs, token rejection, and stored rows.
- [Server audit API](./server-audit.md) covers `/v1/audit` filters and formats, `/v1/roles`, and token separation.
- [MCP tools](./mcp.md) covers `audit_sessions` and `list_roles` over stdio, locally and against the server.
