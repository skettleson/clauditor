# clauditor

clauditor audits coding-agent sessions against the operator's job function. It reads the local transcripts of three agents:

| source | agent | transcripts |
|---|---|---|
| `claude-code` | Claude Code | `~/.claude/projects/*/*.jsonl` |
| `cursor` | Cursor's agent, in the IDE and in `cursor-agent` | `~/.cursor/projects/*/agent-transcripts/<id>/<id>.jsonl` |
| `codex` | Codex, OpenAI's coding agent, in ChatGPT, the Codex app and the Codex CLI | `~/.codex/sessions/**/rollout-*.jsonl`, or under `$CODEX_HOME` |

It classifies every prompt and tool call against one capability catalog and flags sessions that did work the operator's role forbids. Every flag cites the transcript file and line number, plus the event uuid or tool call id where the agent records one, so a reviewer can open the exact line.

It runs on the Python 3.12+ standard library. It has no dependencies and makes no LLM calls.

## Run it on one machine

```
python3 -m clauditor demo
python3 -m clauditor scan
python3 -m clauditor scan --role software-engineer --format markdown > audit.md
python3 -m clauditor scan --only 097b1cbb --show-aligned
python3 -m clauditor scan --source cursor --source codex
python3 -m clauditor scan --fail-on-drift
python3 -m unittest -v
```

`scan` reads every source by default. `--source` limits it to the named agents, and `--claude-code-root`, `--cursor-root` and `--codex-root` point a source at another directory. `scan` exits 1 with `--fail-on-drift` when any session is `drifted`, so you can use it in CI.

Roles live in `policy/roles.toml` and name capabilities only. Detection regexes live in `policy/capabilities.toml`. A role that names an unknown capability fails at load. For the design and its tradeoffs, see `docs/DESIGN.md`.

## How the pieces fit

A team deployment has three parts:

- The **collector** (`clauditor collect`) runs on each laptop on a timer. It parses new or changed Claude Code, Cursor and Codex transcripts locally and posts them to the server as normalized activities. Raw JSONL never leaves the laptop. Tool results, thinking, and assistant prose are dropped. Only prompts and tool-call subjects are sent, and each subject is capped at 20,000 characters.
- The **server** (`clauditor serve`) stores the activities in SQLite and audits them on request against the policy it loaded at startup.
- The **MCP server** gives a reviewer's agent two tools, `audit_sessions` and `list_roles`. It runs two ways. `clauditor mcp` runs on the reviewer's machine over stdio for Claude Code, Cursor and Codex, and calls the server over HTTP. Without `--server`, it audits the local transcripts instead. The server also answers MCP itself at `POST /mcp` (Streamable HTTP), for clients that connect only to a URL.

The collector identifies the operator by the email in `~/.claude.json` (`oauthAccount.emailAddress`) and by the local username. An operator who uses only Cursor or Codex has no `~/.claude.json`, so map their local username under `[users]`. The server resolves each session's role in this order: the `[users]` table in `policy/roles.toml`, then the `[[assign]]` project globs, then `default_role`.

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

## Connect a reviewer's agent

Each client below gets the same two tools. Then ask things like "which sessions drifted this week" or "audit alice@example.com's sessions since 2026-09-01". The `user` filter works only against the server.

There are two ways to connect. The stdio way runs `python3 -m clauditor mcp` from a clone on the reviewer's machine. Point `PYTHONPATH` at the clone so the module resolves from any directory. To audit only the reviewer's own machine, leave out `CLAUDITOR_SERVER` and the token. The URL way connects straight to `https://clauditor.example.com/mcp` with the read token as a bearer header, and needs no clone.

### Claude Code

```
claude mcp add clauditor \
	-e PYTHONPATH=$HOME/clauditor \
	-e CLAUDITOR_SERVER=https://clauditor.example.com \
	-e CLAUDITOR_READ_TOKEN=<read token> \
	-- python3 -m clauditor mcp
```

For the URL way, run `claude mcp add --transport http clauditor https://clauditor.example.com/mcp --header "Authorization: Bearer <read token>"`.

### Cursor

Add this to `~/.cursor/mcp.json`, or to `.cursor/mcp.json` in one project. The IDE and `cursor-agent` both read it.

```json
{
  "mcpServers": {
    "clauditor": {
      "command": "python3",
      "args": ["-m", "clauditor", "mcp"],
      "env": {
        "PYTHONPATH": "/Users/YOU/clauditor",
        "CLAUDITOR_SERVER": "https://clauditor.example.com",
        "CLAUDITOR_READ_TOKEN": "<read token>"
      }
    }
  }
}
```

For the URL way, replace the entry with `{"url": "https://clauditor.example.com/mcp", "headers": {"Authorization": "Bearer <read token>"}}`. Run `cursor-agent mcp list-tools clauditor` to confirm that Cursor sees both tools.

### Codex (the Codex CLI, the Codex app and Codex in ChatGPT)

```
codex mcp add clauditor \
	--env PYTHONPATH=$HOME/clauditor \
	--env CLAUDITOR_SERVER=https://clauditor.example.com \
	--env CLAUDITOR_READ_TOKEN=<read token> \
	-- python3 -m clauditor mcp
```

For the URL way, export `CLAUDITOR_READ_TOKEN` and run `codex mcp add clauditor --url https://clauditor.example.com/mcp --bearer-token-env-var CLAUDITOR_READ_TOKEN`. Both commands write `[mcp_servers.clauditor]` to `~/.codex/config.toml`.

### ChatGPT connectors and the OpenAI API

A ChatGPT developer-mode connector reaches MCP servers only by URL, and it authenticates with OAuth or with nothing. It cannot send a static bearer token, so it cannot use the read token. clauditor does not implement OAuth. Do not expose `/mcp` without a token to get around this, because every audit would become public.

A program on the OpenAI Responses API or the Agents SDK can send headers, so it can use the URL way with `Authorization: Bearer <read token>`.

## Reference

### Commands

| command | does |
|---|---|
| `scan` | Audits local transcripts and prints a report. Takes the source options, `--role`, `--only`, `--format text\|markdown\|json`, `--show-aligned`, and `--fail-on-drift`. |
| `demo` | Audits the bundled fixtures against several roles. |
| `collect` | Ships new or changed local transcripts to `--server`. Takes the source options and `--state-file`. |
| `serve` | Runs the HTTP server. Takes `--host` (default `127.0.0.1`), `--port` (default `8750`), `--db` (default `./clauditor.db`), and `--no-auth` for local testing. |
| `mcp` | Runs the MCP server on stdio. Takes `--server`, or the source options to audit local transcripts. |

The source options are `--source claude-code|cursor|codex`, which can repeat and defaults to all three, and `--claude-code-root`, `--cursor-root` and `--codex-root`. Every command takes `--policy-dir` before the subcommand, for example `python3 -m clauditor --policy-dir /etc/clauditor/policy serve`.

### Environment variables

| variable | used by | meaning |
|---|---|---|
| `CLAUDITOR_SERVER` | `collect`, `mcp` | Server base URL. `--server` overrides it. |
| `CLAUDITOR_INGEST_TOKEN` | `collect`, `serve` | Bearer token for `POST /v1/ingest`. |
| `CLAUDITOR_READ_TOKEN` | `mcp`, `serve` | Bearer token for `GET /v1/audit`, `GET /v1/roles` and `POST /mcp`. |
| `CODEX_HOME` | `scan`, `collect`, `mcp` | Codex's home directory. The default Codex root is `$CODEX_HOME/sessions`, or `~/.codex/sessions`. |

### HTTP API

| endpoint | token | returns |
|---|---|---|
| `GET /healthz` | none | `ok` |
| `POST /v1/ingest` | ingest | `{"stored": N}`. The body is a schema-1 batch from `clauditor/wire.py`, at most 64 MiB. A session with the same host, source, and session id replaces the stored one. |
| `GET /v1/audit` | read | The report. Query parameters are `user` (email or local username), `since` (ISO 8601 start time), `session` (id prefix), `role` (override), `format` (`json` by default, or `text` or `markdown`), and `show_aligned=1`. |
| `GET /v1/roles` | read | Roles, their expected and forbidden capabilities, and the `[users]` map, as JSON. |
| `POST /mcp` | read | MCP over Streamable HTTP, with the same tools as `clauditor mcp`. It answers each JSON-RPC request with one JSON response and each notification with 202. `GET /mcp` returns 405 because the server never pushes messages. |

A missing or wrong token returns 401 with `WWW-Authenticate: Bearer`. A malformed batch, an unknown role, or an unknown format returns 400 with the reason in the body.

## Know what it sends and what it misses

Prompts and shell commands can contain secrets that an operator pasted into a session. The collector sends them to the server as they are. Treat the database and the read token as sensitive as the transcripts themselves.

Detection is regex-based. It misses renamed binaries and hand-written scanners, and it can flag a tool name that appears at the start of a line inside a quoted argument. `docs/DESIGN.md` lists the accepted tradeoffs.

Each agent records less than Claude Code in some places:

- Cursor writes no timestamp, uuid or working directory on transcript lines. clauditor takes each turn's time from the `<timestamp>` Cursor puts in the prompt, and a subagent's time from its file's creation time. It rebuilds the project path from the project folder name by matching it against the local disk. Cursor citations have a line number and no uuid.
- A Codex subagent writes its own rollout file. clauditor audits it as its own session titled `subagent of <parent id>`, instead of folding it into the parent the way it does for Claude Code and Cursor.
- ChatGPT conversations outside Codex live on OpenAI's servers, not on the laptop, so the collector cannot read them.
