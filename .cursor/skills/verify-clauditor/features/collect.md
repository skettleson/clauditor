# Collect and ingest

The collector runs on an operator's laptop. It parses new or changed transcripts, stamps them with the operator's identity, and posts them to the server as normalized activities. The server stores them in SQLite, and a session that already exists is replaced, not duplicated. Only ingest tokens can write.

## Sub-features

- `collect-ship` ships every transcript on the first run and prints `shipped N, unchanged N, failed N`.
- `collect-incremental` skips transcripts whose size and mtime are unchanged since the last run.
- `collect-reship` re-ships a changed transcript, and the server replaces the stored session.
- `collect-auth` fails with exit 1 and `clauditor: last ship error: 401 unauthorized` on a wrong token. Failed transcripts stay pending.
- `collect-identity` stores each session with the host, the local username, and the email from `~/.claude.json`.
- `ingest-reject` makes the server reject the read token (401) and malformed batches (400).

## How to get to it (user POV)

- Run `CLAUDITOR_INGEST_TOKEN=<token> python3 -m clauditor collect --server <url>`, or set `CLAUDITOR_SERVER` instead of `--server`.
- Let the scheduled LaunchAgent or cron entry from the README run it.
- `POST /v1/ingest` with a schema-1 batch. The collector is the only real client.

## Driving it with verify.sh

Preconditions:

- `RUN=<id> $V setup` and `RUN=<id> $V serve` have run. The DB is empty, and `$V_STATE` does not exist.
- `RUN=<id> $V doctor` reports no `FAIL`.

- **First ship.** Run `RUN=<id> $V capture collect/first 'python3 -m clauditor collect --server "$V_URL" --root "$V_ROOT" --state-file "$V_STATE"'`. Exit is `0`. stdout is `shipped 2, unchanged 0, failed 0`.
- **Stored rows.** Read the DB. Run `RUN=<id> $V capture collect/rows 'sqlite3 "$V_DB" "select session_id, email, local_user, project_cwd from sessions order by 1; select count(*) from activities;"'`. You see the rows `coding_session|<your email>|<your username>|/Users/dev/code/acme-web` and `drift_session|...`, then `11`.
- **Server sees it.** Run `RUN=<id> $V capture collect/audit 'curl -s -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/audit?format=text&show_aligned=1"'`. The findings and the summary `2 sessions: 1 drifted, 0 review, 1 aligned, 0 unassigned` match `scan` on the same root.
- **Incremental.** Run the same collect again. Run `RUN=<id> $V capture collect/second 'python3 -m clauditor collect --server "$V_URL" --root "$V_ROOT" --state-file "$V_STATE"'`. stdout is `shipped 0, unchanged 2, failed 0`. `$V_STATE` holds 2 keys, one per transcript path.
- **Re-ship replaces.** Change one transcript the way a live session would, by appending an event. Run `RUN=<id> $V capture collect/grow 'python3 -c "import pathlib, sys; p = next(pathlib.Path(sys.argv[1]).glob(\"*/drift_session.jsonl\")); lines = p.read_text().splitlines(); p.write_text(\"\n\".join(lines + [lines[-1]]) + \"\n\")" "$V_ROOT"'`. Then run `RUN=<id> $V capture collect/reship 'python3 -m clauditor collect --server "$V_URL" --root "$V_ROOT" --state-file "$V_STATE"'`. stdout is `shipped 1, unchanged 1, failed 0`. Then run `RUN=<id> $V capture collect/reship-rows 'sqlite3 "$V_DB" "select session_id, count(*) from activities group by 1 order by 1;"'`. `drift_session` goes from `3` to `4`, and there are still 2 sessions.
- **Wrong token.** Use a separate state file so the good state stays intact. Run `RUN=<id> $V capture collect/bad-token 'CLAUDITOR_INGEST_TOKEN=wrong python3 -m clauditor collect --server "$V_URL" --root "$V_ROOT" --state-file "$V_STATE.bad"'`. Exit is `1`. stdout is `shipped 0, unchanged 0, failed 2`, and stderr is `clauditor: last ship error: 401 unauthorized`.
- **Read token cannot write.** Run `RUN=<id> $V capture collect/read-token 'curl -s -w " [%{http_code}]" -X POST -H "authorization: Bearer $V_READ_TOKEN" -d "{}" "$V_URL/v1/ingest"'`. stdout is `unauthorized` with ` [401]`.
- **Malformed batch.** Run `RUN=<id> $V capture collect/malformed 'curl -s -w " [%{http_code}]" -X POST -H "authorization: Bearer $V_INGEST_TOKEN" -d "{}" "$V_URL/v1/ingest"'`. stdout is `unsupported schema None, expected 1` with ` [400]`.
- **Proof.** Keep `collect/first`, `collect/rows`, `collect/audit`, and `collect/second`. Together they show the ship, the stored side effect, the reviewer-visible result, and the incremental skip.

## Gotchas

- Without `--root` and `--state-file`, collect ships your real transcripts and overwrites the real collector's `~/.clauditor/collector-state.json`. Always pass both.
- `collect` has no default server. Without `--server` or `CLAUDITOR_SERVER`, it exits 2 with `clauditor: collect needs --server or CLAUDITOR_SERVER`. `capture` unsets `CLAUDITOR_SERVER` on purpose.
- The email comes from the real `~/.claude.json`, and `local_user` from the login name, so the stored identity differs from one machine to the next. Assert that the fields are present, not their literal values.
- The first run counts from an empty state file. Reusing a `RUN` after a collect makes "first ship" report `unchanged`.
- The collector ships subagent transcripts as part of their parent session. `coding_session` includes `subagents/agent-a1b2c3.jsonl`, so 1 of its 8 activities is stored with `subagent_id` `a1b2c3`.
