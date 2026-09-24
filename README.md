# clauditor

clauditor audits Claude Code sessions against the operator's job function. It reads the transcripts in `~/.claude/projects`, classifies every prompt and tool call against a capability catalog, and flags sessions that did work the operator's role forbids. Every flag cites the transcript file, line number, and event uuid, so a reviewer can open the exact line.

It runs on the Python 3.12+ standard library. It has no dependencies and makes no LLM calls.

## Run it on one machine

```
python3 -m clauditor demo
python3 -m clauditor scan
python3 -m clauditor scan --role software-engineer --format markdown > audit.md
python3 -m clauditor scan --only 097b1cbb --show-aligned
python3 -m clauditor scan --fail-on-drift
python3 -m unittest -v
```

`scan` exits 1 with `--fail-on-drift` when any session is `drifted`, so you can use it in CI.

Roles live in `policy/roles.toml` and name capabilities only. Detection regexes live in `policy/capabilities.toml`. A role that names an unknown capability fails at load. For the design and its tradeoffs, see `docs/DESIGN.md`.

## How the pieces fit

A team deployment has three parts:

- The **collector** (`clauditor collect`) runs on each laptop on a timer. It parses new or changed transcripts locally and posts them to the server as normalized activities. Raw JSONL never leaves the laptop. Tool results, thinking, and assistant prose are dropped. Only prompts and tool-call subjects are sent, and each subject is capped at 20,000 characters.
- The **server** (`clauditor serve`) stores the activities in SQLite and audits them on request against the policy it loaded at startup.
- The **MCP server** (`clauditor mcp`) runs on a reviewer's machine and gives their Claude Code two tools, `audit_sessions` and `list_roles`. It calls the server over HTTP. Without `--server`, it audits the local `~/.claude/projects` instead.

The collector identifies the operator by the email in `~/.claude.json` (`oauthAccount.emailAddress`) and by the local username. The server resolves each session's role in this order: the `[users]` table in `policy/roles.toml`, then the `[[assign]]` project globs, then `default_role`.

Two separate bearer tokens protect the server. Collectors hold `CLAUDITOR_INGEST_TOKEN` and can only write. Reviewers hold `CLAUDITOR_READ_TOKEN` and can read every audit. The server refuses to start if the tokens are equal.

## Deploy the server

These steps target a Linux host with systemd. The server speaks plain HTTP, so put a TLS proxy in front of it before collectors send data over any network you do not control.

1. Create a service user and install the code.

	```
	sudo useradd --system --home /var/lib/clauditor --create-home clauditor
	sudo git clone <this repo> /opt/clauditor
	```

2. Generate two different tokens.

	```
	python3 -c "import secrets; print(secrets.token_urlsafe(32))"
	python3 -c "import secrets; print(secrets.token_urlsafe(32))"
	```

3. Write them to `/etc/clauditor.env` and make the file readable only by root.

	```
	CLAUDITOR_INGEST_TOKEN=<first token>
	CLAUDITOR_READ_TOKEN=<second token>
	```

	```
	sudo chmod 600 /etc/clauditor.env
	```

4. Edit `/opt/clauditor/policy/roles.toml`. Map each operator's email to a role under `[users]`. Unmapped operators fall through to `[[assign]]` and `default_role`.

5. Create `/etc/systemd/system/clauditor.service`.

	```ini
	[Unit]
	Description=clauditor audit server
	After=network.target

	[Service]
	User=clauditor
	WorkingDirectory=/opt/clauditor
	EnvironmentFile=/etc/clauditor.env
	ExecStart=/usr/bin/python3 -m clauditor serve --host 127.0.0.1 --port 8750 --db /var/lib/clauditor/clauditor.db
	Restart=on-failure
	NoNewPrivileges=true
	ProtectSystem=strict
	ReadWritePaths=/var/lib/clauditor

	[Install]
	WantedBy=multi-user.target
	```

6. Start the service and confirm that it answers.

	```
	sudo systemctl daemon-reload
	sudo systemctl enable --now clauditor
	curl -s localhost:8750/healthz
	```

	The last command prints `ok`.

7. Put TLS in front of it. With Caddy, this `Caddyfile` gets a certificate and proxies to the server:

	```
	clauditor.example.com {
		reverse_proxy 127.0.0.1:8750
	}
	```

The server loads the policy once at startup. After you edit `policy/roles.toml` or `policy/capabilities.toml`, run `sudo systemctl restart clauditor`. A policy error stops the start and shows in `journalctl -u clauditor`. Audits are computed on every request, so a policy change applies to all stored sessions and needs no re-collect.

To back up the database while the server runs, use `sqlite3 /var/lib/clauditor/clauditor.db ".backup /var/backups/clauditor.db"`.

## Install the collector on each laptop

The collector needs a clone of this repo, `python3` 3.12 or later, the server URL, and the ingest token.

1. Clone the repo to `~/clauditor`.
2. Run one collection by hand to confirm the token and URL.

	```
	cd ~/clauditor
	CLAUDITOR_INGEST_TOKEN=<ingest token> python3 -m clauditor collect --server https://clauditor.example.com
	```

	It prints `shipped N, unchanged N, failed N`. The first run ships every transcript. Later runs ship only transcripts whose size or modification time changed. The collector records what it shipped in `~/.clauditor/collector-state.json`. If you delete that file, the next run ships everything again, and the server replaces each session instead of duplicating it.

3. Schedule it. On macOS, save this as `~/Library/LaunchAgents/com.clauditor.collector.plist`, replacing the three placeholders:

	```xml
	<?xml version="1.0" encoding="UTF-8"?>
	<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
	<plist version="1.0">
	<dict>
		<key>Label</key><string>com.clauditor.collector</string>
		<key>WorkingDirectory</key><string>/Users/YOU/clauditor</string>
		<key>ProgramArguments</key>
		<array>
			<string>/usr/bin/env</string><string>python3</string>
			<string>-m</string><string>clauditor</string><string>collect</string>
		</array>
		<key>EnvironmentVariables</key>
		<dict>
			<key>CLAUDITOR_SERVER</key><string>https://clauditor.example.com</string>
			<key>CLAUDITOR_INGEST_TOKEN</key><string>INGEST_TOKEN</string>
			<key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
		</dict>
		<key>StartInterval</key><integer>900</integer>
		<key>RunAtLoad</key><true/>
		<key>StandardErrorPath</key><string>/tmp/clauditor-collector.log</string>
	</dict>
	</plist>
	```

	```
	chmod 600 ~/Library/LaunchAgents/com.clauditor.collector.plist
	launchctl load ~/Library/LaunchAgents/com.clauditor.collector.plist
	```

	On Linux, add a crontab line instead:

	```
	*/15 * * * * cd ~/clauditor && CLAUDITOR_SERVER=https://clauditor.example.com CLAUDITOR_INGEST_TOKEN=<ingest token> python3 -m clauditor collect
	```

`collect` exits 1 when any batch failed, and it writes the last server error to stderr, for example `clauditor: last ship error: 401 unauthorized`. Failed transcripts stay pending and retry on the next run.

## Connect a reviewer's Claude Code

Register the MCP server with Claude Code. Point `PYTHONPATH` at the clone so `python3 -m clauditor` resolves from any directory.

```
claude mcp add clauditor \
	-e PYTHONPATH=$HOME/clauditor \
	-e CLAUDITOR_SERVER=https://clauditor.example.com \
	-e CLAUDITOR_READ_TOKEN=<read token> \
	-- python3 -m clauditor mcp
```

Then ask Claude things like "which sessions drifted this week" or "audit alice@example.com's sessions since 2026-09-01". To audit only your own machine, leave out `CLAUDITOR_SERVER` and the token. The `user` filter works only against the server.

## Reference

### Commands

| command | does |
|---|---|
| `scan` | Audits local transcripts and prints a report. Takes `--root`, `--role`, `--only`, `--format text\|markdown\|json`, `--show-aligned`, and `--fail-on-drift`. |
| `demo` | Audits the bundled fixtures against several roles. |
| `collect` | Ships new or changed local transcripts to `--server`. Takes `--root` and `--state-file`. |
| `serve` | Runs the HTTP server. Takes `--host` (default `127.0.0.1`), `--port` (default `8750`), `--db` (default `./clauditor.db`), and `--no-auth` for local testing. |
| `mcp` | Runs the MCP server on stdio. Takes `--server` or `--root`. |

Every command takes `--policy-dir` before the subcommand, for example `python3 -m clauditor --policy-dir /etc/clauditor/policy serve`.

### Environment variables

| variable | used by | meaning |
|---|---|---|
| `CLAUDITOR_SERVER` | `collect`, `mcp` | Server base URL. `--server` overrides it. |
| `CLAUDITOR_INGEST_TOKEN` | `collect`, `serve` | Bearer token for `POST /v1/ingest`. |
| `CLAUDITOR_READ_TOKEN` | `mcp`, `serve` | Bearer token for `GET /v1/audit` and `GET /v1/roles`. |

### HTTP API

| endpoint | token | returns |
|---|---|---|
| `GET /healthz` | none | `ok` |
| `POST /v1/ingest` | ingest | `{"stored": N}`. The body is a schema-1 batch from `clauditor/wire.py`, at most 64 MiB. A session with the same host, source, and session id replaces the stored one. |
| `GET /v1/audit` | read | The report. Query parameters are `user` (email or local username), `since` (ISO 8601 start time), `session` (id prefix), `role` (override), `format` (`json` by default, or `text` or `markdown`), and `show_aligned=1`. |
| `GET /v1/roles` | read | Roles, their expected and forbidden capabilities, and the `[users]` map, as JSON. |

A missing or wrong token returns 401. A malformed batch, an unknown role, or an unknown format returns 400 with the reason in the body.

## Know what it sends and what it misses

Prompts and shell commands can contain secrets that an operator pasted into a session. The collector sends them to the server as they are. Treat the database and the read token as sensitive as the transcripts themselves.

Detection is regex-based. It misses renamed binaries and hand-written scanners, and it can flag a tool name that appears at the start of a line inside a quoted argument. `docs/DESIGN.md` lists the accepted tradeoffs.
