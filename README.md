# clauditor

Audits Claude Code sessions in `~/.claude/projects` against the operator's job function and cites the exact transcript line behind every flag.

```
python3 -m clauditor demo
python3 -m clauditor scan
python3 -m clauditor scan --role software-engineer --format markdown > audit.md
python3 -m clauditor scan --only 097b1cbb --show-aligned
python3 -m clauditor scan --fail-on-drift
python3 -m unittest -v
```

Roles live in `policy/roles.toml` and name capabilities only. Detection regexes live in `policy/capabilities.toml`. A role that names an unknown capability fails at load. Design and tradeoffs: `docs/DESIGN.md`.
