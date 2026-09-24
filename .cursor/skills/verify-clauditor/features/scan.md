# Scan local transcripts

Scan audits the Claude Code transcripts under a projects root against each session's role. It prints a verdict per session (drifted, review, aligned, or unassigned) and cites the transcript file, line, and event uuid behind every forbidden finding. Demo runs the same audit over the bundled fixtures, once per role.

## Sub-features

- `scan-verdict` assigns each session a verdict and drift points under its resolved role.
- `scan-cite` cites every forbidden finding as `file:line uuid`.
- `scan-role` audits every session under the role given in `--role`, and rejects unknown roles.
- `scan-only` limits the scan to sessions whose id starts with the `--only` prefix.
- `scan-format` renders the report as `text`, `markdown`, or `json`. `--show-aligned` adds the aligned sessions.
- `scan-fail` exits 1 under `--fail-on-drift` when any session drifted.
- `demo` audits `fixtures/coding_session.jsonl` as `software-engineer` and as `support-analyst`.

## How to get to it (user POV)

- Run `python3 -m clauditor scan` with any of the flags above. Without `--root`, it reads `~/.claude/projects`.
- Run `python3 -m clauditor demo`.
- Run `python3 -m clauditor --policy-dir <dir> scan` to audit against another policy.

## Driving it with verify.sh

Preconditions:

- `RUN=<id> $V setup` has run. No server is needed.

- **Default scan.** Scan the run's root. Run `RUN=<id> $V capture scan/default 'python3 -m clauditor scan --root "$V_ROOT"'`. Exit is `0`. stdout shows `DRIFTED    drift 20  software-engineer    drift_se` with `FORBIDDEN network-scanning (high)  shell  nmap -p 5432 10.0.0.0/24` cited as `drift_session.jsonl:3 uuid 000000d2`, and `credential-attack` cited as `drift_session.jsonl:4 uuid 000000d3`. The summary line is `2 sessions: 1 drifted, 0 review, 1 aligned, 0 unassigned`.
- **Citation is real.** Open the cited line. Run `RUN=<id> $V capture scan/cited-line 'sed -n 3p "$V_ROOT"/*/drift_session.jsonl'`. The line contains `"uuid": "000000d2-` and `nmap -p 5432 10.0.0.0/24`.
- **Show aligned.** Run `RUN=<id> $V capture scan/aligned 'python3 -m clauditor scan --root "$V_ROOT" --show-aligned'`. An `ALIGNED    drift 0   software-engineer  "Fix NaN cart total after coupon removal"` block appears, with `in-role: edit-source x2, run-tests x1, version-control x1`.
- **Role override and prefix.** Run `RUN=<id> $V capture scan/support 'python3 -m clauditor scan --root "$V_ROOT" --only codi --role support-analyst'`. Exit is `0`. Only the coding session appears, as `DRIFTED    drift 2   support-analyst`, with `FORBIDDEN edit-source` at `coding_session.jsonl:10` and `:13`, and `FORBIDDEN version-control` at `coding_session.jsonl:19`. The summary line is `1 sessions: 1 drifted`.
- **Unknown role.** Run `RUN=<id> $V capture scan/bad-role 'python3 -m clauditor scan --root "$V_ROOT" --role nope'`. Exit is `2`. stderr is `policy error: unknown role 'nope', known: ['security-engineer', 'software-engineer', 'support-analyst']`.
- **JSON and CI gate.** Run `RUN=<id> $V capture scan/json-gate 'python3 -m clauditor scan --root "$V_ROOT" --format json --fail-on-drift'`. Exit is `1`. stdout is JSON with `summary.sessions` `2` and `summary.drifted` `1`. `sessions[0].violations[*]` holds `capability`, `line`, `uuid`, and the absolute `transcript` path under `$V_ROOT`.
- **Markdown.** Run `RUN=<id> $V capture scan/markdown 'python3 -m clauditor scan --root "$V_ROOT" --format markdown'`. stdout starts with `# clauditor report` and has a `## DRIFTED: drift_session` table.
- **Demo.** Run `RUN=<id> $V capture scan/demo 'python3 -m clauditor demo'`. Exit is `0`. It shows `=== coding_session.jsonl as software-engineer` as `ALIGNED` and `=== coding_session.jsonl as support-analyst` as `DRIFTED    drift 2`. Both `pentest_session.jsonl` cases print `fixture missing, skipped`.
- **Proof.** Keep `scan/default` and `scan/cited-line` together. They show the finding and the transcript line it points at.

## Gotchas

- Without `--root`, scan reads the operator's real `~/.claude/projects`. The output changes from one machine to the next and may include private prompts. Always pass `--root "$V_ROOT"`.
- Transcripts are found only at `<root>/*/*.jsonl`. A root that points directly at a directory of `.jsonl` files finds 0 sessions and exits 0.
- Session ids come from the transcript file name (`drift_session`), not from the `sessionId` field. Text output truncates them to 8 characters.
- JSON and markdown leave out aligned sessions unless you pass `--show-aligned`, but `summary` still counts them.
- `fixtures/pentest_session.jsonl` is not in the repo, so its demo cases and the matching unit tests are skipped. That is expected, not a failure.
