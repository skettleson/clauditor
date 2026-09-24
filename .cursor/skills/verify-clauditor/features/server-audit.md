# Server audit API

A reviewer asks the server for an audit of everything collected. The server audits the stored sessions against the policy it loaded at startup, on every request. It filters by operator, start time, session, or role override, and returns JSON, text, or markdown. Only the read token can read, and the ingest token cannot.

## Sub-features

- `audit-report` makes `GET /v1/audit` return the report for every stored session, drifted and review first.
- `audit-filter` narrows the report with `user` (email or local username), `since` (ISO 8601), and `session` (id prefix).
- `audit-role` makes `role=<name>` audit every session as that role. An unknown role returns 400.
- `audit-format` accepts `format=json|text|markdown`. `show_aligned=1` adds aligned sessions, and an unknown format returns 400.
- `roles` makes `GET /v1/roles` return roles, expected and forbidden capabilities, `default_role`, and the `[users]` map.
- `token-separation` returns 401 for a missing token and for the ingest token on read endpoints. `/healthz` needs no token.

## How to get to it (user POV)

- `curl -H "authorization: Bearer <read token>" <server>/v1/audit?...`
- `curl -H "authorization: Bearer <read token>" <server>/v1/roles`
- Indirectly, through the MCP server's `--server` mode. See [MCP tools](./mcp.md).

## Driving it with verify.sh

Preconditions:

- `RUN=<id> $V setup` and `RUN=<id> $V serve` have run, and `RUN=<id> $V doctor` reports no `FAIL`.
- Both seed sessions are ingested. Run `RUN=<id> $V capture audit/seed 'python3 -m clauditor collect --server "$V_URL" --root "$V_ROOT" --state-file "$V_STATE"'`, which prints `shipped 2`.

- **Full report.** Run `RUN=<id> $V capture audit/markdown 'curl -s -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/audit?format=markdown&show_aligned=1"'`. stdout starts with `# clauditor report`, then `## DRIFTED: drift_session` with rows for `network-scanning` and `credential-attack` citing `drift_session.jsonl:3 uuid 000000d2` and `:4 uuid 000000d3`. `## ALIGNED: Fix NaN cart total after coupon removal` comes after. The last line is `2 sessions: 1 drifted, 0 review, 1 aligned, 0 unassigned`.
- **Default JSON.** Run `RUN=<id> $V capture audit/json 'curl -s -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/audit"'`. The body is JSON with `summary.drifted` `1`, and `sessions` holds only `drift_session`.
- **User filter.** Run `RUN=<id> $V capture audit/user 'curl -s -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/audit?format=text&show_aligned=1&user=$(id -un)"; echo; curl -s -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/audit?format=text&user=nobody@example.com"'`. The first report ends with `2 sessions: ...`. The second is `0 sessions: 0 drifted, 0 review, 0 aligned, 0 unassigned`.
- **Since filter.** Run `RUN=<id> $V capture audit/since 'curl -s -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/audit?format=text&show_aligned=1&since=2026-09-21T00:00:00Z"'`. Only `drift_session` (started `2026-09-21T09:00:00.000Z`) remains, and the summary line is `1 sessions: 1 drifted`.
- **Session and role.** Run `RUN=<id> $V capture audit/role 'curl -s -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/audit?format=text&session=codi&role=support-analyst"'`. The first line is `DRIFTED    drift 2   support-analyst  "Fix NaN cart total after coupon removal"`.
- **Bad input.** Run `RUN=<id> $V capture audit/bad 'curl -s -w " [%{http_code}]\n" -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/audit?format=xml"; curl -s -w " [%{http_code}]\n" -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/audit?role=nope"'`. You see `format must be one of ['json', 'markdown', 'text']` with ` [400]`, then `unknown role 'nope', known: [...]` with ` [400]`.
- **Roles.** Run `RUN=<id> $V capture audit/roles 'curl -s -H "authorization: Bearer $V_READ_TOKEN" "$V_URL/v1/roles"'`. The JSON has `default_role` `software-engineer`, `roles.support-analyst.forbidden` containing `edit-source`, and a `users` map.
- **Tokens.** Run `RUN=<id> $V doctor`. It checks 401 with no token, 401 with the ingest token, and 200 with the read token on `/v1/roles`. Keep its output as `audit/doctor` by running `RUN=<id> $V capture audit/doctor '"$V_HELPER" doctor'`.
- **Proof.** Keep `audit/markdown` and one filter step. When the change touches policy loading, also keep `server.log`, which cleanup copies into evidence.

## Gotchas

- The server loads the policy once, at startup. After editing `policy/*.toml`, run `RUN=<id> $V stop` and then `RUN=<id> $V serve` again. Audits are computed per request, so no re-collect is needed.
- The server refuses to start if the tokens are equal, or if either is missing without `--no-auth`. The helper always sets two distinct tokens.
- Without `show_aligned=1`, JSON leaves aligned sessions out of `sessions`, but `summary` still counts them.
- The `user` filter matches the email or the local username the collector recorded. Those come from the machine that ran `collect`, not from the transcript.
