import json
import tempfile
import unittest
from pathlib import Path

from clauditor.audit import audit
from clauditor.model import Verdict
from clauditor.policy import PolicyError, load_policy
from clauditor.formats.claude_code import load_session

ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = ROOT / "policy"
FIXTURES = ROOT / "fixtures"
CODING = FIXTURES / "coding_session.jsonl"
PENTEST = FIXTURES / "pentest_session.jsonl"
POLICY = load_policy(POLICY_DIR / "capabilities.toml", POLICY_DIR / "roles.toml")


def audit_as(transcript: Path, role: str):
    return audit([load_session(transcript)], POLICY, role_override=role)[0]


def violation_citations(result):
    return sorted((f.capability.name, f.activity.evidence.transcript.name, f.activity.evidence.line_no) for f in result.violations)


def bash_session(tmp: Path, command: str) -> Path:
    transcript = tmp / "s.jsonl"
    event = {
        "type": "assistant",
        "uuid": "u1",
        "timestamp": "2026-09-20T10:00:00.000Z",
        "cwd": "/w",
        "sessionId": "s",
        "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": command}}]},
    }
    transcript.write_text(json.dumps(event) + "\n")
    return transcript


def capabilities_hit(tmp: Path, command: str) -> set[str]:
    return {f.capability.name for f in audit_as(bash_session(tmp, command), "software-engineer").findings}


class DemoFixturesTest(unittest.TestCase):
    def test_coding_session_is_aligned_for_software_engineer(self) -> None:
        result = audit_as(CODING, "software-engineer")
        self.assertIs(result.verdict, Verdict.ALIGNED)
        self.assertEqual(result.drift_points, 0)
        self.assertEqual(sorted({f.capability.name for f in result.findings}), ["edit-source", "run-tests", "version-control"])

    def test_coding_session_drifts_for_support_analyst_with_cited_lines(self) -> None:
        result = audit_as(CODING, "support-analyst")
        self.assertIs(result.verdict, Verdict.DRIFTED)
        self.assertEqual(result.drift_points, 2)
        self.assertEqual(
            violation_citations(result),
            [("edit-source", "coding_session.jsonl", 10), ("edit-source", "coding_session.jsonl", 13), ("version-control", "coding_session.jsonl", 19)],
        )

    def test_subagent_activity_is_folded_into_parent_with_its_id(self) -> None:
        tests_run = [f for f in audit_as(CODING, "software-engineer").findings if f.capability.name == "run-tests"]
        self.assertEqual([(f.activity.evidence.subagent_id, f.activity.evidence.line_no, f.activity.subject) for f in tests_run], [("a1b2c3", 3, "npm test -- CartTotal")])

    def test_only_human_prompts_become_prompt_activities(self) -> None:
        prompts = [a.subject for a in load_session(CODING).activities if a.kind.value == "prompt"]
        self.assertEqual(prompts, ["The cart total shows NaN when a coupon is removed. Fix it and add a test."])

    def test_role_naming_unknown_capability_is_rejected_at_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roles = Path(tmp) / "roles.toml"
            roles.write_text('[roles.typo]\nexpected = ["edit-sauce"]\n')
            with self.assertRaisesRegex(PolicyError, "edit-sauce"):
                load_policy(POLICY_DIR / "capabilities.toml", roles)


class CommandPositionTest(unittest.TestCase):
    def test_tool_names_only_match_in_command_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self.assertEqual(capabilities_hit(tmp, "cat /home/john/notes.txt"), set())
            self.assertEqual(capabilities_hit(tmp, "grep -rn make src"), set())
            self.assertEqual(capabilities_hit(tmp, "make build"), {"build-and-package"})
            self.assertEqual(capabilities_hit(tmp, "cd app && npm test"), {"run-tests"})
            self.assertEqual(capabilities_hit(tmp, "sudo -l"), {"privilege-escalation"})

    def test_heredoc_bodies_are_data_not_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(capabilities_hit(Path(tmp), "cat > notes.md <<'EOF'\nsudo -l\nEOF"), set())


@unittest.skipUnless(PENTEST.exists(), "fixtures/pentest_session.jsonl not provided")
class PentestFixtureTest(unittest.TestCase):
    def test_pentest_session_drifts_for_software_engineer(self) -> None:
        self.assertIs(audit_as(PENTEST, "software-engineer").verdict, Verdict.DRIFTED)

    def test_pentest_session_is_aligned_for_security_engineer(self) -> None:
        self.assertIs(audit_as(PENTEST, "security-engineer").verdict, Verdict.ALIGNED)

    def test_every_violation_cites_transcript_line_and_uuid(self) -> None:
        for f in audit_as(PENTEST, "software-engineer").violations:
            self.assertGreater(f.activity.evidence.line_no, 0)
            self.assertTrue(f.activity.evidence.event_uuid)


if __name__ == "__main__":
    unittest.main()
