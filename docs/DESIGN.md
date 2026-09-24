# clauditor design

## Problem

Audit Claude Code sessions in `~/.claude/projects` and say, per session, whether the work matched the operator's job function, with a score and the exact tool call behind every flag. The transcripts are noisy JSONL: most events are metadata, assistant turns are split into one event per content block, and subagent work lives in separate files under `<sessionId>/subagents/`. Pentest tooling often runs inside those subagents, so a design that reads only the top-level file will miss it. The constraints are a POC that runs today on stdlib python3.14 or node 26 with no API key, roles a non-programmer can edit, explainability over accuracy, and a repeatable demo proving that a pentest session is flagged for a software engineer and a coding session is not.

## Usage (caller's view)

README quickstart:

```
python3 -m clauditor demo
python3 -m clauditor scan
python3 -m clauditor scan --role software-engineer --format markdown > audit.md
python3 -m clauditor scan --only 6300a1be --show-aligned
python3 -m clauditor scan --fail-on-drift
```

`demo` audits the two bundled fixtures (`fixtures/pentest_session.jsonl`, `fixtures/coding_session.jsonl`) against `software-engineer` and prints both verdicts. Run it in front of a reviewer.

Text report (drifted sessions first, then sessions needing review):

```
DRIFTED  drift 30  software-engineer  "Probe staging login"  3f2a91c0
  project  /Users/sam/code/acme-api   2026-09-20T14:02:11Z   41 activities, 6 unclassified
  FORBIDDEN network-scanning (high)    shell  nmap -sV -p- 10.0.4.0/24
            3f2a91c0.jsonl:118 uuid 8e1d...  subagent a17f
  FORBIDDEN credential-attack (high)   shell  hydra -L users.txt -P rockyou.txt ssh://10.0.4.12
            3f2a91c0.jsonl:131 uuid 02bc...
  FORBIDDEN web-exploitation (high)    shell  sqlmap -u https://staging.acme.com/login --dump
            3f2a91c0.jsonl:140 uuid 77aa...
  in-role: edit-source x4, version-control x2

REVIEW   drift 3   software-engineer  "Fix sudo install script"  c01d44e2
  FORBIDDEN privilege-escalation (medium)  shell  sudo -l
            c01d44e2.jsonl:57 uuid 5f0e...

212 sessions: 1 drifted, 1 review, 204 aligned, 6 unassigned
```

A non-programmer edits `policy/roles.toml`. The catalog in `policy/capabilities.toml` holds the regexes and belongs to whoever owns detection:

```toml
[roles.software-engineer]
description = "Builds and maintains product code."
expected  = ["edit-source", "run-tests", "build-and-package", "version-control", "read-docs"]
forbidden = ["network-scanning", "web-exploitation", "credential-attack", "exploit-development"]
drift_threshold = 10

[[assign]]
project = "*-pentest-*"
role = "security-engineer"
```

Library call sites:

```python
from clauditor.transcripts import load_sessions
from clauditor.policy import load_policy
from clauditor.audit import audit
from clauditor.report import render

policy = load_policy(Path("policy/capabilities.toml"), Path("policy/roles.toml"))
audits = audit(load_sessions(), policy)
print(render(audits, "markdown"))
```

```python
result = audit([load_session(FIXTURES / "pentest_session.jsonl")], policy, role_override="software-engineer")[0]
assert result.verdict is Verdict.DRIFTED
assert {f.capability.name for f in result.violations} >= {"network-scanning", "credential-attack"}
assert all(f.activity.evidence.line_no > 0 for f in result.violations)
```

## Shape

Module map, in `sketch/clauditor/`, with one call chain `__main__ -> audit -> model`:

| module | owns |
|---|---|
| `model.py` | all domain types, no I/O |
| `transcripts.py` | JSONL knowledge: event types, content blocks, tool-name to kind mapping, subagent folding |
| `policy.py` | TOML parsing and cross-validation of roles against the catalog |
| `audit.py` | pure classification and scoring |
| `report.py` | text, markdown and json rendering |
| `__main__.py` | argparse, `scan`, `demo` |

**Activity** is the unit of evidence: one human prompt or one `tool_use` block, normalized to `(kind, tool_name, subject, evidence)`. `kind` is a closed enum (`prompt, shell, file_read, file_write, web, mcp, tool`). `subject` is the one string worth matching for that kind: the Bash `command`, the `file_path`, the URL or search query, the MCP tool name plus serialized input, or the prompt text. `EvidenceRef` holds the transcript path, 1-based line number, event uuid, timestamp and subagent id. It is the single source of truth for "where did this happen", and the report quotes only from it. Tool results, thinking and assistant prose are dropped because they are what the model said, not what the session did. Wire JSON never leaves `transcripts.py`, per boundary-discipline.

**Session** is one top-level transcript with its subagent transcripts folded in, ordered by timestamp. The `isSidechain`/subagent origin is kept on each activity's evidence, not as a separate session, because the operator owns the whole tree.

**Capability** is a named, reusable kind of work (`network-scanning`, `edit-source`) with a severity and a list of `Matcher(kinds, regex)`. The catalog is the only place detection logic lives.

**Role** is only names: `expected` and `forbidden` capability sets, with everything else implicitly `TOLERATED`. `Stance` is a three-valued enum, not a boolean, because "not expected" is not "forbidden". A data scientist running nmap is odd, but a software engineer running `curl` is not. `load_policy` rejects any role that names a capability missing from the catalog, so a typo fails at load time instead of silently never matching (per encode-lessons-in-structure). Session to role resolution is ordered `[[assign]]` globs over the project cwd, then `default_role`, with `--role` overriding. A session with no role gets `UNASSIGNED`, never a guessed verdict.

**Classification** is deterministic: `classify(activity, catalog)` returns every capability whose matcher kind set includes the activity kind and whose regex matches `subject`, along with the matched substring. One activity can carry several capabilities. An activity with no match counts toward `unclassified_count` and is otherwise ignored.

**Scoring**: `drift_points` = sum of severity (low 1, medium 3, high 10) over **distinct** forbidden capabilities hit. Running nmap 40 times is still one capability, so a noisy loop can't inflate the score and repeating an audit is idempotent.
- Verdict: `DRIFTED` if `drift_points >= role.drift_threshold`, `REVIEW` if `0 < drift_points < threshold`, else `ALIGNED`. With the defaults, one high-severity forbidden capability drifts a session, and a lone medium one asks for review.

**Report** lists the forbidden findings first, each with the matched command and `file:line uuid`, so a reviewer can `sed -n 118p` the transcript. `json` emits the same structure for later tooling.

**Proof** comes from two handwritten fixture transcripts in the real event shape: a pentest session where nmap runs in a subagent to exercise folding, and a React bugfix session. Five unittest cases cover this. The pentest fixture is DRIFTED for software-engineer and ALIGNED for security-engineer, which shows the verdict comes from the role and not a global blocklist. The coding fixture is ALIGNED. Every violation cites a line and uuid. A bad role name fails at load. `python3 -m clauditor demo` prints the same result for a human.

Interface depth: four public functions (`load_sessions`, `load_policy`, `audit`, `render`) plus frozen dataclasses. They hide JSONL quirks, subagent folding, tool-name mapping, regex compilation, cross-file validation, stance resolution and the scoring policy. Callers never see raw events or TOML dicts.

The design deliberately leaves out any LLM call, any persistence or cache, and any incremental or streaming mode. A full scan of about 40 files is a sub-second stdlib pass.

## Synthesis decision

Two runners designed this independently (Opus, Sonnet). Both converged on the same core: an Activity is one human prompt or one `tool_use` block, a single shared capability catalog owns all detection regexes, and roles are allow/deny lists of capability names. That convergence is the strongest signal in the arena, so the consensus shape ships and no cross-judge was run.

Base: the Opus candidate. It uses stdlib `tomllib` where the other needed PyYAML, it has a concrete role-assignment story (cwd globs, `--role` override, UNASSIGNED), it folds subagent transcripts into the parent session, and its proof plan is five named tests over two fixtures.

Grafted:
- Shell-tool regexes are anchored at command position (`(^|[;&|(]\s*|sudo\s+)tool\b`) so `john`, `make`, `nc` inside paths or prose do not match. Open question in the base, answered here.
- Both candidates named "rules first, LLM judge on the unclassified residue, constrained to the closed capability vocabulary" as v2. `unclassified_count` stays in the output as the seam.

Removed from the base: `alignment_pct`. Verdict plus `drift_points` already ranks sessions, and a second score invites arguing about which one matters.

Rejected from the other candidate: YAML config (third-party dependency), glob patterns over dotted capability tags in roles (a second matching language for non-programmers), a `Classifier` protocol with an LLM implementation in the POC (surface without a demo that needs it), and a markdown renderer living on the model type.

## Implementation deviations

- `run_scan` takes `only` for the `--only` flag the usage already showed.
- Catalog regexes use a `{cmd}` token that `policy.py` expands to "start of a command": line start, after `;` `&` `|` `(` backtick `$(`, or after `sudo`/`xargs`/`exec`. Catalog authors write `{cmd}hydra\b`, not `\bhydra\b`.
- Shell subjects have heredoc bodies stripped in `transcripts.py`. The first real-data scan flagged this very session because heredocs writing regex text like `(nmap|masscan)` read as a pipe into `masscan`. A heredoc body is data unless piped into a shell, and that case is accepted as a miss.
- A `support-analyst` role was added so the role-relative verdict is provable on the benign coding fixture: the same session is ALIGNED for software-engineer and DRIFTED for support-analyst.
- `coding_session` runs `npm test` inside a subagent transcript, so subagent folding is tested without the pentest fixture.
- `fixtures/pentest_session.jsonl` is not in the repo. The implementing agent was stopped by a safety classifier while generating it, and it was not regenerated around that stop. Its three tests are `skipUnless` the file exists, and `demo` prints "fixture missing" for its two cases.

## Tradeoffs accepted

- We accept missing novel or obfuscated attacks (a renamed binary, a raw-socket Python scanner) in exchange for verdicts that are deterministic, free, offline, unit-testable, and explained by exactly one regex and one line.
- We accept that prompt matchers will false-positive on discussion ("explain what nmap does") in exchange for catching intent the tools don't reveal. Prompt matchers are few and are where severity should be tuned down first.
- We accept splitting policy into two files, with roles for non-programmers and the regex catalog for the detection owner, in exchange for roles that are just lists of plain-English names.
- We accept counting distinct forbidden capabilities rather than occurrences, which undercounts sustained activity in exchange for idempotent, non-gameable scores.
- We accept false positives when a tool name appears at the start of a line inside a quoted argument (for example `python3 -c "...\nnmap --version"`), in exchange for not writing a shell parser. Measured on 177 real sessions: 1 flag, and it is this class.
- We accept reading the whole transcript into memory per session, since the POC data is small.

## Alternatives considered

- **LLM judge per session** (`claude -p` with the role description and a compressed activity list). It hides everything behind "ask the model", but its verdicts are non-deterministic, slow over hundreds of sessions, hard to test in CI, and its evidence citation can be hallucinated unless re-checked against events. That re-check is the rule engine anyway. It lost on explainability, the stated priority.
- **Hybrid: rules first, LLM on the unclassified residue.** This is the strongest contender and the natural v2. It lost for the POC because it doubles the surface (prompting, parsing, caching verdicts, handling judge failures) before the rule-based demo has shown the idea works. `unclassified_count` is kept in the output so the gap is visible.
- **Roles as inline allow/deny regexes** (no catalog). This makes the smallest config, but a non-programmer would have to write regexes and every role would re-state the same nmap pattern, which is information leakage across roles.
- **Per-event scoring stream** (time-series of drift). It is richer for spotting when a session turned, but it is temporal decomposition, and a reviewer at POC stage wants the per-session verdict.

## Open questions and risks

- How is a session's operator identified? The transcripts carry no user identity, so role assignment by project glob assumes one operator per laptop. Is that acceptable for the demo, or should `--role` be the only mechanism?
- Should the report surface activity against external hosts (non-RFC1918 IPs, unknown domains in shell commands) as its own capability? It is a strong signal that separates recon from local testing, but it needs a small host extractor rather than a regex.
- Short-token regexes (`john`, `make`, `nc`) will false-positive. Should the catalog require command-position anchoring (`(^|[;&|]\s*)hydra\b`) by convention?
- Is a two-file policy too much for the "non-programmer edits it" goal, or is editing only `roles.toml` good enough?
- Fixture realism: the fixtures must mirror the one-block-per-event assistant shape seen in real transcripts, or folding and line-number bugs will hide until real data is run.

## Next implementation step

Write `transcripts.load_session` and the two fixture JSONL files together, and assert that the pentest fixture yields shell activities with correct line numbers and subagent ids before touching classification.
