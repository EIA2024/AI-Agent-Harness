"""Contract tests for legacy CLI command behavior (CLI v2 Phase 0, T02).

These tests pin the observable output/exit-code behavior of the pre-v2
commands. They must keep passing (or change only deliberately) as the CLI is
rebuilt.
"""

from __future__ import annotations

import argparse

import pytest

from personal_ai_os.cli import legacy


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


# ---------------------------------------------------------------------------
# _ensure_session
# ---------------------------------------------------------------------------


def test_ensure_session_reuses_given_id(fake_api, capsys):
    fake_api({})
    session_id = legacy._ensure_session(legacy.APIClient(), "abc-123")
    assert session_id == "abc-123"
    assert capsys.readouterr().out == ""


def test_ensure_session_creates_when_missing(fake_api, capsys):
    api = fake_api({("POST", "/v1/sessions"): {"id": "new-1"}})
    session_id = legacy._ensure_session(legacy.APIClient(), None)
    assert session_id == "new-1"
    assert "[new session new-1]" in capsys.readouterr().out
    assert ("POST", "/v1/sessions") in [(c.method, c.url.path) for c in api.calls]


# ---------------------------------------------------------------------------
# _print_reply
# ---------------------------------------------------------------------------


def test_print_reply_completed(fake_api, capsys):
    fake_api(
        {
            ("GET", "/v1/runs/r1"): {
                "status": "completed",
                "state": {"final_response": "hello world"},
            }
        }
    )
    legacy._print_reply(legacy.APIClient(), "r1")
    assert capsys.readouterr().out.strip() == "hello world"


def test_print_reply_waiting_approval(fake_api, capsys):
    fake_api({("GET", "/v1/runs/r1"): {"status": "waiting_approval", "state": {}}})
    legacy._print_reply(legacy.APIClient(), "r1")
    assert "审批" in capsys.readouterr().out


def test_print_reply_failed_prints_error(fake_api, capsys):
    fake_api(
        {
            ("GET", "/v1/runs/r1"): {
                "status": "failed",
                "state": {},
                "error": {"message": "boom"},
            }
        }
    )
    legacy._print_reply(legacy.APIClient(), "r1")
    assert "Run failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_sessions / cmd_runs
# ---------------------------------------------------------------------------


def test_cmd_sessions_list_prints_rows(fake_api, capsys):
    fake_api(
        {
            ("GET", "/v1/sessions"): [
                {"id": "s1", "status": "active", "title": "hello world"},
                {"id": "s2", "status": "archived", "title": None},
            ]
        }
    )
    legacy.cmd_sessions(_args(action="list"))
    out = capsys.readouterr().out
    assert "s1" in out and "active" in out and "hello world" in out
    assert "s2" in out and "archived" in out


def test_cmd_runs_list_iterates_active_run_ids(fake_api, capsys):
    fake_api(
        {
            ("GET", "/v1/sessions"): [
                {"id": "s1", "active_run_id": "r1"},
                {"id": "s2", "active_run_id": "r2"},
                {"id": "s3", "active_run_id": None},
            ],
            ("GET", "/v1/runs/r1"): {"id": "r1", "status": "completed", "session_id": "s1"},
            ("GET", "/v1/runs/r2"): {"id": "r2", "status": "running", "session_id": "s2"},
        }
    )
    legacy.cmd_runs(_args(action="list", limit=10))
    out = capsys.readouterr().out
    assert "r1" in out and "r2" in out
    assert "No runs" not in out


def test_cmd_runs_list_skips_bad_run(fake_api, capsys):
    fake_api(
        {
            ("GET", "/v1/sessions"): [{"id": "s1", "active_run_id": "r1"}],
            ("GET", "/v1/runs/r1"): 500,
        }
    )
    legacy.cmd_runs(_args(action="list", limit=10))
    assert "No runs found." in capsys.readouterr().out


def test_cmd_runs_get_prints_json(fake_api, capsys):
    fake_api({("GET", "/v1/runs/r1"): {"id": "r1", "status": "completed"}})
    legacy.cmd_runs(_args(action="get", run_id="r1"))
    import json

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["id"] == "r1"


# ---------------------------------------------------------------------------
# cmd_memories
# ---------------------------------------------------------------------------


def test_cmd_memories_search(fake_api, capsys):
    fake_api(
        {
            ("POST", "/v1/memories/search"): [
                {"id": "m1", "type": "fact", "content": "user likes rust"}
            ]
        }
    )
    legacy.cmd_memories(_args(action="search", query="rust", limit=10))
    out = capsys.readouterr().out
    assert "m1" in out and "rust" in out


def test_cmd_memories_list(fake_api, capsys):
    fake_api({("GET", "/v1/memories"): [{"id": "m1", "type": "fact", "content": "abc"}]})
    legacy.cmd_memories(_args(action="list"))
    assert "m1" in capsys.readouterr().out


def test_cmd_memories_forget(fake_api, capsys):
    api = fake_api({("DELETE", "/v1/memories/m1"): {"id": "m1", "status": "forgotten"}})
    legacy.cmd_memories(_args(action="forget", memory_id="m1"))
    assert ("DELETE", "/v1/memories/m1") in [(c.method, c.url.path) for c in api.calls]
    assert "forgotten" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_approve
# ---------------------------------------------------------------------------


def test_cmd_approve_list(fake_api, capsys):
    fake_api(
        {
            ("GET", "/v1/approvals"): [
                {"id": "a1", "tool_name": "filesystem.read", "action_summary": "read file"}
            ]
        }
    )
    legacy.cmd_approve(_args(list=True, approval_id=None))
    out = capsys.readouterr().out
    assert "a1" in out and "filesystem.read" in out


def test_cmd_approve_with_id(fake_api, capsys):
    api = fake_api(
        {("POST", "/v1/approvals/a1/approve"): {"id": "a1", "status": "approved"}}
    )
    legacy.cmd_approve(_args(list=False, approval_id="a1"))
    assert ("POST", "/v1/approvals/a1/approve") in [
        (c.method, c.url.path) for c in api.calls
    ]
    assert "approved" in capsys.readouterr().out


def test_cmd_approve_without_id_exits_2(fake_api, capsys):
    fake_api({})
    with pytest.raises(SystemExit) as exc:
        legacy.cmd_approve(_args(list=False, approval_id=None))
    assert exc.value.code == 2
