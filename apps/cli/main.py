"""Personal AI OS command-line client (stdlib argparse + httpx).

Usage::

    personal-ai chat                          # interactive chat
    personal-ai send "text" --session <id>    # single message
    personal-ai sessions list
    personal-ai runs list --limit 10
    personal-ai memories search "query"
    personal-ai approve --list                # list pending approvals

Environment:
    PERSONAL_AI_API_URL  (default http://localhost:8000)
    PERSONAL_AI_API_KEY  (default dev-key)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

API_URL = os.environ.get("PERSONAL_AI_API_URL", "http://localhost:8000")
API_KEY = os.environ.get("PERSONAL_AI_API_KEY", "dev-key")


class APIClient:
    """Thin sync HTTP client for the Personal AI OS REST API."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or API_URL).rstrip("/")
        self.api_key = api_key or API_KEY
        self._http = httpx.Client(timeout=60)

    def request(self, method: str, path: str, **kwargs) -> dict:
        kwargs.setdefault("headers", {"X-API-Key": self.api_key})
        response = self._http.request(method, f"{self.base_url}{path}", **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:  # noqa: BLE001
                detail = response.text
            print(f"Error {response.status_code}: {detail}", file=sys.stderr)
            raise SystemExit(1)
        return response.json()

    def get(self, path: str, **kwargs) -> dict:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> dict:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs) -> dict:
        return self.request("DELETE", path, **kwargs)


def _ensure_session(client: APIClient, session_id: str | None) -> str:
    if session_id:
        return session_id
    created = client.post("/v1/sessions", json={"channel": "cli"})
    print(f"[new session {created['id']}]")
    return created["id"]


def _print_reply(client: APIClient, run_id: str) -> None:
    """Fetch a completed run and print its final assistant response."""
    run = client.get(f"/v1/runs/{run_id}")
    status = run.get("status", "unknown")

    if status == "completed":
        final = (run.get("state") or {}).get("final_response")
        if final:
            print(final)
        return
    if status == "waiting_approval":
        print("  ⏸  等待人工审批（`personal-ai approve --list` 查看）")
        return
    err = run.get("error") or {}
    print(f"  ⚠  Run {status}: {err.get('message') or err.get('code') or ''}".rstrip())


def cmd_chat(args: argparse.Namespace) -> None:
    """Interactive chat: loop stdin -> POST message -> print result + reply."""
    client = APIClient()
    session_id = _ensure_session(client, args.session)
    while True:
        try:
            text = input("> ")
        except (EOFError, KeyboardInterrupt):
            break
        if not text.strip():
            continue
        resp = client.post(f"/v1/sessions/{session_id}/messages", json={"text": text})
        run_id = resp.get("run_id")
        status = resp.get("status", "running")
        print(f"[run {run_id} status={status}]")
        if run_id:
            _print_reply(client, run_id)


def cmd_send(args: argparse.Namespace) -> None:
    client = APIClient()
    session_id = _ensure_session(client, args.session)
    resp = client.post(f"/v1/sessions/{session_id}/messages", json={"text": args.text})
    run_id = resp.get("run_id")
    status = resp.get("status", "running")
    print(f"[run {run_id} status={status}]")
    if run_id:
        _print_reply(client, run_id)


def cmd_sessions(args: argparse.Namespace) -> None:
    client = APIClient()
    if args.action == "list":
        data = client.get("/v1/sessions")
        for s in data:
            title = (s.get("title") or "").replace("\n", " ")[:40]
            print(f"{s['id']}  {s.get('status'):9}  {title}")


def cmd_runs(args: argparse.Namespace) -> None:
    client = APIClient()
    if args.action == "list":
        sessions = client.get("/v1/sessions")
        runs: list[dict] = []
        for s in sessions:
            run_id = s.get("active_run_id")
            if not run_id:
                continue
            try:
                runs.append(client.get(f"/v1/runs/{run_id}"))
            except SystemExit:
                continue
            if len(runs) >= args.limit:
                break
        if not runs:
            print("No runs found.")
            return
        for r in runs:
            session_id = (r.get("session_id") or "?")[:12]
            print(f"{r['id']}  {r.get('status'):12}  session={session_id}")
        return
    if args.action == "get":
        run = client.get(f"/v1/runs/{args.run_id}")
        print(json.dumps(run, ensure_ascii=False, indent=2, default=str))


def cmd_memories(args: argparse.Namespace) -> None:
    client = APIClient()
    if args.action == "search":
        data = client.post(
            "/v1/memories/search",
            json={"query": args.query, "limit": getattr(args, "limit", 10)},
        )
        for m in data:
            print(f"{m.get('id')}  [{m.get('type')}]  {(m.get('content') or '')[:60]}")
    elif args.action == "list":
        data = client.get("/v1/memories")
        for m in data:
            print(f"{m.get('id')}  [{m.get('type')}]  {(m.get('content') or '')[:60]}")
    elif args.action == "forget":
        resp = client.delete(f"/v1/memories/{args.memory_id}")
        print(json.dumps(resp, ensure_ascii=False))


def cmd_approve(args: argparse.Namespace) -> None:
    client = APIClient()
    if args.list:
        data = client.get("/v1/approvals", params={"status": "pending"})
        for a in data:
            print(f"{a['id']}  {a.get('tool_name')}: {a.get('action_summary')}")
        return
    if not args.approval_id:
        print("Provide an approval id or use --list.", file=sys.stderr)
        raise SystemExit(2)
    resp = client.post(f"/v1/approvals/{args.approval_id}/approve")
    print(json.dumps(resp, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="personal-ai",
        description="Personal AI OS command-line client",
    )
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="start an interactive chat session")
    chat.add_argument("--session", help="existing session id (default: create one)")
    chat.set_defaults(func=cmd_chat)

    send = sub.add_parser("send", help="send a single message")
    send.add_argument("text", help="message text")
    send.add_argument("--session", help="session id (default: create one)")
    send.set_defaults(func=cmd_send)

    sessions = sub.add_parser("sessions", help="manage sessions")
    sessions_sub = sessions.add_subparsers(dest="action")
    sessions_list = sessions_sub.add_parser("list", help="list sessions")
    sessions_list.set_defaults(func=cmd_sessions)

    runs = sub.add_parser("runs", help="manage runs")
    runs_sub = runs.add_subparsers(dest="action")
    runs_list = runs_sub.add_parser("list", help="list recent runs")
    runs_list.add_argument("--limit", type=int, default=10)
    runs_list.set_defaults(func=cmd_runs)
    runs_get = runs_sub.add_parser("get", help="show a run")
    runs_get.add_argument("run_id")
    runs_get.set_defaults(func=cmd_runs)

    memories = sub.add_parser("memories", help="manage memories")
    memories_sub = memories.add_subparsers(dest="action")
    mem_search = memories_sub.add_parser("search", help="search memories")
    mem_search.add_argument("query", help="search text")
    mem_search.add_argument("--limit", type=int, default=10)
    mem_search.set_defaults(func=cmd_memories)
    mem_list = memories_sub.add_parser("list", help="list memories")
    mem_list.set_defaults(func=cmd_memories)
    mem_forget = memories_sub.add_parser("forget", help="forget a memory")
    mem_forget.add_argument("memory_id")
    mem_forget.set_defaults(func=cmd_memories)

    approve = sub.add_parser("approve", help="approve pending actions")
    approve.add_argument("--list", action="store_true", help="list pending approvals")
    approve.add_argument("approval_id", nargs="?", help="approval id to approve")
    approve.set_defaults(func=cmd_approve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
