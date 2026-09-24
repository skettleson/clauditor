import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from clauditor.collector import CollectResult, collect
from clauditor.model import Owner
from clauditor.policy import load_policy
from clauditor.server import ServerConfig, make_server

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
POLICY = load_policy(ROOT / "policy" / "capabilities.toml", ROOT / "policy" / "roles.toml")
INGEST, READ = "ingest-secret", "read-secret"
OWNER = Owner("laptop-1", "dev", "skettleson@gmail.com")
CODING_ID = "coding_session"


def make_projects_root(tmp: Path) -> Path:
    project = tmp / "projects" / "-Users-dev-code-acme-web"
    project.mkdir(parents=True)
    shutil.copy(FIXTURES / "coding_session.jsonl", project)
    shutil.copytree(FIXTURES / "coding_session", project / "coding_session")
    return tmp / "projects"


class ServerTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.projects = make_projects_root(self.tmp)
        self.server = make_server("127.0.0.1", 0, ServerConfig(self.tmp / "db.sqlite", POLICY, INGEST, READ))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def collect(self, token: str = INGEST) -> CollectResult:
        return collect(self.url, token, self.projects, self.tmp / "state.json", OWNER)

    def get(self, path: str, token: str | None = READ) -> tuple[int, str]:
        request = urllib.request.Request(self.url + path, headers={"authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def test_collected_session_is_audited_under_the_owners_mapped_role(self) -> None:
        self.assertEqual(self.collect(), CollectResult(shipped=1, unchanged=0, failed=0))
        status, body = self.get("/v1/audit?user=skettleson@gmail.com&show_aligned=1")
        self.assertEqual(status, 200)
        [session] = json.loads(body)["sessions"]
        self.assertEqual((session["session_id"], session["role"], session["verdict"]), (CODING_ID, "software-engineer", "aligned"))

    def test_role_override_turns_the_same_stored_session_into_drift_with_citations(self) -> None:
        self.collect()
        [session] = json.loads(self.get("/v1/audit?role=support-analyst")[1])["sessions"]
        self.assertEqual(session["verdict"], "drifted")
        self.assertEqual(sorted({v["capability"] for v in session["violations"]}), ["edit-source", "version-control"])
        self.assertTrue(all(v["line"] > 0 and v["uuid"] for v in session["violations"]))

    def test_recollecting_unchanged_transcripts_ships_nothing(self) -> None:
        self.collect()
        self.assertEqual(self.collect(), CollectResult(shipped=0, unchanged=1, failed=0))

    def test_reshipping_a_grown_transcript_replaces_the_stored_session(self) -> None:
        self.collect()
        transcript = self.projects / "-Users-dev-code-acme-web" / "coding_session.jsonl"
        extra = {"type": "assistant", "uuid": "u-extra", "timestamp": "2026-09-20T11:00:00.000Z", "sessionId": CODING_ID, "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "nmap -sV 10.0.0.1"}}]}}
        with transcript.open("a") as f:
            f.write(json.dumps(extra) + "\n")
        self.assertEqual(self.collect(), CollectResult(shipped=1, unchanged=0, failed=0))
        [session] = json.loads(self.get("/v1/audit")[1])["sessions"]
        self.assertEqual([(v["capability"], v["subject"]) for v in session["violations"]], [("network-scanning", "nmap -sV 10.0.0.1")])

    def test_forked_transcript_sharing_a_session_id_is_stored_as_its_own_session(self) -> None:
        shutil.copy(self.projects / "-Users-dev-code-acme-web" / "coding_session.jsonl", self.projects / "-Users-dev-code-acme-web" / "f0f0f0f0-fork.jsonl")
        self.assertEqual(self.collect(), CollectResult(shipped=2, unchanged=0, failed=0))
        sessions = json.loads(self.get("/v1/audit?show_aligned=1")[1])["sessions"]
        self.assertEqual(sorted(s["session_id"] for s in sessions), ["coding_session", "f0f0f0f0-fork"])

    def test_ingest_rejects_the_read_token_and_audit_rejects_the_ingest_token(self) -> None:
        self.assertEqual(self.collect(token=READ), CollectResult(shipped=0, unchanged=0, failed=1, last_error="401 unauthorized"))
        self.assertEqual(self.get("/v1/audit", token=INGEST), (401, "unauthorized\n"))
        self.assertEqual(self.get("/v1/audit", token=None), (401, "unauthorized\n"))

    def test_malformed_batch_is_a_400_naming_the_field(self) -> None:
        request = urllib.request.Request(self.url + "/v1/ingest", data=json.dumps({"schema": 1, "owner": {"host": "h"}, "sessions": []}).encode(), headers={"authorization": f"Bearer {INGEST}"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        with caught.exception as error:
            self.assertEqual((error.code, error.read().decode()), (400, "local_user must be a non-empty string\n"))

    def test_unknown_role_is_a_400(self) -> None:
        self.assertEqual(self.get("/v1/audit?role=astronaut")[0], 400)

    def test_mcp_answers_through_the_server(self) -> None:
        self.collect()
        replies = run_mcp(["--server", self.url], {"CLAUDITOR_READ_TOKEN": READ}, [("tools/call", {"name": "audit_sessions", "arguments": {"role": "support-analyst"}})])
        text = replies[1]["result"]["content"][0]["text"]
        self.assertIn("## DRIFTED: Fix NaN cart total after coupon removal", text)
        self.assertIn("| edit-source | low |", text)


class LocalMcpTest(unittest.TestCase):
    def test_handshake_lists_tools_and_audits_local_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_projects_root(Path(tmp))
            replies = run_mcp(
                ["--root", str(root)],
                {},
                [
                    ("tools/list", {}),
                    ("tools/call", {"name": "audit_sessions", "arguments": {"show_aligned": True, "format": "json"}}),
                    ("tools/call", {"name": "audit_sessions", "arguments": {"user": "someone"}}),
                    ("no/such", {}),
                ],
            )
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], ["audit_sessions", "list_roles"])
        audited = json.loads(replies[2]["result"]["content"][0]["text"])
        self.assertEqual([(s["session_id"], s["verdict"]) for s in audited["sessions"]], [(CODING_ID, "aligned")])
        self.assertEqual(replies[3]["result"]["isError"], True)
        self.assertEqual(replies[4]["error"]["code"], -32601)


def run_mcp(args: list[str], env: dict[str, str], calls: list[tuple[str, dict]]) -> list[dict]:
    messages = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}]
    messages.append({"jsonrpc": "2.0", "method": "notifications/initialized"})
    messages += [{"jsonrpc": "2.0", "id": i, "method": method, "params": params} for i, (method, params) in enumerate(calls, start=1)]
    completed = subprocess.run(
        [sys.executable, "-m", "clauditor", "mcp", *args],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "HOME": str(ROOT)} | env,
        timeout=30,
    )
    return [json.loads(line) for line in completed.stdout.splitlines()]


if __name__ == "__main__":
    unittest.main()
