import tempfile
import unittest
from pathlib import Path

from clauditor.audit import audit
from clauditor.formats import load_sessions
from clauditor.formats.cursor import workspace_path
from clauditor.model import Verdict
from clauditor.policy import load_policy

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
POLICY = load_policy(ROOT / "policy" / "capabilities.toml", ROOT / "policy" / "roles.toml")
CURSOR_ID = "c0de1111-1111-4222-8333-444455556666"
CODEX_ID = "c0de2222-1111-4222-8333-444455556666"
CODEX_CHILD_ID = "c0de3333-1111-4222-8333-444455556666"
PROMPT = "The checkout button stays disabled after the address form validates. Fix it and add a test."


def sessions(source: str) -> dict:
    return {s.session_id: s for s in load_sessions({source: FIXTURES / source})}


def activity_rows(session) -> list[tuple]:
    return [(a.kind.value, a.tool_name, a.subject, a.evidence.line_no, a.evidence.subagent_id) for a in session.activities]


def verdicts(session, role: str) -> tuple:
    [result] = audit([session], POLICY, role)
    return result.verdict, sorted((f.capability.name, f.activity.evidence.line_no) for f in result.violations)


class CursorFormatTest(unittest.TestCase):
    def test_session_is_normalized_with_turn_timestamps_and_folded_subagent(self) -> None:
        session = sessions("cursor")[CURSOR_ID]
        self.assertEqual((session.source, session.project_cwd, session.started_at), ("cursor", "/Users/dev/code/shop", "2026-09-20T15:00:00.000Z"))
        self.assertEqual(
            activity_rows(session),
            [
                ("prompt", None, PROMPT, 1, None),
                ("file_read", "Read", "/Users/dev/code/shop/src/checkout/CheckoutButton.tsx", 2, None),
                ("file_read", "Grep", "isValid", 3, None),
                ("file_write", "StrReplace", "/Users/dev/code/shop/src/checkout/CheckoutButton.tsx", 4, None),
                ("file_write", "ApplyPatch", "/Users/dev/code/shop/src/checkout/CheckoutButton.test.tsx", 5, None),
                ("tool", "Task", '{"description": "Run checkout tests", "prompt": "Run the checkout tests and report failures.", "subagent_type": "shell"}', 6, None),
                ("mcp", "mcp__linear__get_issue", 'mcp__linear__get_issue {"id": "SHOP-12"}', 7, None),
                ("web", "WebSearch", "react-hook-form isValid stays false after reset", 8, None),
                ("prompt", None, "Commit it.", 10, None),
                ("shell", "Shell", "git add -A && git commit -m 'Enable checkout button after validation'", 11, None),
                ("shell", "Shell", "npm test -- CheckoutButton", 2, "5ub0000a-1111-4222-8333-444455556666"),
            ],
        )

    def test_verdict_depends_on_role(self) -> None:
        session = sessions("cursor")[CURSOR_ID]
        self.assertEqual(verdicts(session, "software-engineer"), (Verdict.ALIGNED, []))
        self.assertEqual(verdicts(session, "support-analyst"), (Verdict.DRIFTED, [("edit-source", 4), ("edit-source", 5), ("version-control", 11)]))

    def test_workspace_slug_resolves_dashed_directory_names_against_the_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "code" / "my-app.v2").mkdir(parents=True)
            self.assertEqual(workspace_path("code-my-app-v2", Path(tmp)), str(Path(tmp) / "code" / "my-app.v2"))
            self.assertEqual(workspace_path("code-gone-app", Path(tmp)), str(Path(tmp) / "code" / "gone" / "app"))
        self.assertEqual(workspace_path("empty-window"), "")


class CodexFormatTest(unittest.TestCase):
    def test_rollout_is_normalized_from_function_custom_and_web_search_calls(self) -> None:
        session = sessions("codex")[CODEX_ID]
        self.assertEqual((session.source, session.project_cwd, session.started_at, session.title), ("codex", "/Users/dev/code/shop", "2026-09-20T15:00:00.000Z", None))
        self.assertEqual(
            activity_rows(session),
            [
                ("prompt", None, PROMPT, 5, None),
                ("shell", "exec_command", "rg -n isValid src", 7, None),
                ("file_write", "apply_patch", "src/checkout/CheckoutButton.tsx\nsrc/checkout/CheckoutButton.test.tsx", 9, None),
                ("shell", "shell", "npm test -- CheckoutButton", 11, None),
                ("web", "web_search", "react-hook-form isValid stays false after reset", 13, None),
                ("mcp", "mcp__linear__get_issue", 'mcp__linear__get_issue {"id": "SHOP-12"}', 14, None),
                ("shell", "exec_command", "git commit -am 'Enable checkout button after validation'", 16, None),
            ],
        )
        self.assertEqual([a.evidence.event_uuid for a in session.activities], ["", "call_01", "call_02", "call_03", "ws_04", "call_05", "call_06"])

    def test_verdict_depends_on_role(self) -> None:
        session = sessions("codex")[CODEX_ID]
        self.assertEqual(verdicts(session, "software-engineer"), (Verdict.ALIGNED, []))
        self.assertEqual(verdicts(session, "support-analyst"), (Verdict.DRIFTED, [("edit-source", 9), ("version-control", 16)]))

    def test_subagent_rollout_is_its_own_session_without_the_parents_instruction_as_a_prompt(self) -> None:
        child = sessions("codex")[CODEX_CHILD_ID]
        self.assertEqual(child.title, f"subagent of {CODEX_ID}")
        self.assertEqual(activity_rows(child), [("shell", "exec_command", "npm run lint", 3, None)])


if __name__ == "__main__":
    unittest.main()
