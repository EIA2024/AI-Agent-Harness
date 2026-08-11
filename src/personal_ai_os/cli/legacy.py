"""Legacy Personal AI OS CLI (stdlib argparse + httpx) — compatibility path.

This module is the pre-v2 command-line client, preserved verbatim so that
``personal-ai chat / send / sessions / runs / memories / approve / config``
keep working during and after the CLI v2 upgrade. New commands live in the
``commands`` / ``controllers`` packages; the v2 Typer root in ``main.py``
delegates legacy command names here.

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

from personal_ai_os.model_gateway import KNOWN_PROVIDERS, ProviderConfigStore, ProviderProfile

API_URL = os.environ.get("PERSONAL_AI_API_URL", "http://localhost:8000")
API_KEY = os.environ.get("PERSONAL_AI_API_KEY", "dev-key")


class APIClient:
    """Thin sync HTTP client for the Personal AI OS REST API."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 timeout: float = 300.0):
        # 300s: reasoning models (e.g. DeepSeek v4-flash) can "think" for a
        # while, and a tool loop makes several model calls before replying.
        self.base_url = (base_url or API_URL).rstrip("/")
        self.api_key = api_key or API_KEY
        self._http = httpx.Client(timeout=timeout)

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


# ANSI: dim/gray for the chain of thought, reset after.
_DIM = "\033[90m"
_RESET = "\033[0m"


def _stream_run(client: APIClient, run_id: str, *, show_thinking: bool = True) -> None:
    """Subscribe to the run's SSE stream and render events live.

    Mirrors the streaming UX of Codex / Kimi / GLM CLIs: the chain of thought
    streams dimmed, tool calls are announced, and the final answer types out.
    Falls back to a plain fetch if the stream errors out.
    """
    import json as _json

    saw_text = False
    try:
        with client._http.stream(
            "GET",
            f"{client.base_url}/v1/runs/{run_id}/stream",
            headers={"X-API-Key": client.api_key},
            timeout=300.0,
        ) as resp:
            if resp.status_code >= 400:
                _print_reply(client, run_id)
                return
            event = None
            data_buf: list[str] = []

            def _flush() -> None:
                nonlocal event, data_buf, saw_text
                if event and data_buf:
                    payload = _json.loads("\n".join(data_buf)) if data_buf else {}
                    if event == "text.delta":
                        saw_text = True
                    _apply(event, payload, show_thinking)
                event = None
                data_buf = []

            for raw in resp.iter_lines():
                line = (raw or "").strip()
                if line == "":
                    _flush()
                elif line.startswith("event:"):
                    event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_buf.append(line[len("data:"):].strip())
            _flush()  # stream may end without a trailing blank line
    except Exception:  # noqa: BLE001 - fall back to non-streaming
        _print_reply(client, run_id)
        return

    if not saw_text:
        # The stream ended without a streamed answer (e.g. tool-only path that
        # never emitted text.delta). Show the final reply from the run record.
        _print_reply(client, run_id)


def _apply(event: str, payload: dict, show_thinking: bool) -> bool:
    """Render one SSE event; returns True for text/thinking deltas."""
    if event == "thinking.delta":
        if show_thinking:
            sys.stdout.write(f"{_DIM}{payload.get('text', '')}{_RESET}")
            sys.stdout.flush()
        return True
    if event == "text.delta":
        sys.stdout.write(payload.get("text", ""))
        sys.stdout.flush()
        return True
    if event == "tool.requested":
        name = payload.get("tool_name", "")
        sys.stdout.write(f"\n  ⚙  调用工具: {name}\n")
        sys.stdout.flush()
    elif event == "tool.completed" or event == "tool.failed":
        pass  # keep it quiet; the tool call line is enough
    elif event in ("run.completed", "run.failed", "approval.required", "run.cancelled"):
        sys.stdout.write("\n")
        sys.stdout.flush()
    return False


def cmd_chat(args: argparse.Namespace) -> None:
    """Interactive chat: POST a message, then stream the run's response live."""
    client = APIClient()
    session_id = _ensure_session(client, args.session)
    show_thinking = not getattr(args, "no_thinking", False)

    if not ProviderConfigStore().get_active() and sys.stdin.isatty():
        print("提示：服务器未配置 LLM provider（可能处于 demo 回显模式）。")
        print("      运行 `personal-ai config init` 配置 OpenAI/Anthropic/DeepSeek 等，")
        print("      然后重启 API 服务即可用真实模型。")
        print()

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
            _stream_run(client, run_id, show_thinking=show_thinking)


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


# ---------------------------------------------------------------------------
# config — local provider profiles (multi-API, secrets stay out of git)
# ---------------------------------------------------------------------------


def _prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    try:
        value = input(f"{text}{suffix} ")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(130)
    value = value.strip()
    return value or (default or "")


def _prompt_secret(text: str) -> str:
    """Prompt for a secret, hiding input when possible (falls back to plain)."""
    try:
        import getpass

        value = getpass.getpass(f"{text} ")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(130)
    except Exception:  # noqa: BLE001 - non-tty fallback
        return _prompt(text)
    return value.strip()


def _wizard_init() -> None:
    store = ProviderConfigStore()
    print("=== Personal AI OS · LLM Provider 配置向导 ===")
    print("本配置保存在本地（仓库外），不会进入 GitHub。")
    print()
    print("选择 API 请求格式：")
    print("  1. OpenAI 兼容格式 — OpenAI / DeepSeek / Moonshot(Kimi) / GLM / Qwen / Ollama ...")
    print("  2. Anthropic 格式 — Claude 官方 API")
    choice = _prompt("选择", "1")
    if choice == "2":
        fmt, base_url, def_model = "anthropic", "", "claude-sonnet-5"
        preset = KNOWN_PROVIDERS["anthropic"]
    else:
        fmt = "openai"
        print()
        print("常用提供商（可输入序号，或直接回车用 OpenAI）：")
        openai_names = [k for k, v in KNOWN_PROVIDERS.items() if v["format"] == "openai"]
        for i, name in enumerate(openai_names, 1):
            p = KNOWN_PROVIDERS[name]
            print(f"  {i}. {name:<10} {p['base_url']}")
        pick = _prompt("选择序号或留空", "")
        if pick and pick.isdigit() and 1 <= int(pick) <= len(openai_names):
            preset = KNOWN_PROVIDERS[openai_names[int(pick) - 1]]
        else:
            preset = KNOWN_PROVIDERS["openai"]
        base_url = preset["base_url"]
        def_model = preset["model"]
        if pick and not pick.isdigit():
            print(f"未识别的选择 {pick!r}，使用 OpenAI 默认值。")
        print()
        custom_url = _prompt(f"Base URL（API 根地址，默认 {base_url}）", base_url)
        base_url = custom_url or base_url
        custom_model = _prompt(f"默认模型（默认 {def_model}）", def_model)
        def_model = custom_model or def_model

    print()
    name = _prompt("配置名称（便于多套切换）", "main")
    api_key = _prompt_secret("API Key")
    if not api_key:
        print("未输入 API Key，取消配置。", file=sys.stderr)
        raise SystemExit(1)

    profile = ProviderProfile(
        name=name,
        format=fmt,
        api_key=api_key,
        base_url=base_url,
        model=def_model,
    )
    store.add(profile, activate=True)
    print()
    print(f"✅ 已保存配置「{name}」（{fmt}，{base_url}）并设为当前使用。")
    print("   重启 API 服务后生效；用 `personal-ai config list/use` 切换多套配置。")


def cmd_config(args: argparse.Namespace) -> None:
    store = ProviderConfigStore()
    action = args.action

    if action == "init":
        _wizard_init()
        return

    if action == "list":
        profiles = store.list_profiles()
        active = store.get_active_name()
        if not profiles:
            print("尚未配置任何 provider。运行 `personal-ai config init` 开始。")
            return
        print(f"{'名称':<12}{'格式':<10}{'模型':<16}{'Base URL':<40}Key")
        for p in profiles:
            marker = "▶ " if p.name == active else "  "
            print(f"{marker}{p.name:<10}{p.format:<10}{(p.model or '-'):<16}{(p.base_url or '-')[:38]:<40}{p.masked_key()}")
        return

    if action == "use":
        if store.set_active(args.name):
            print(f"已切换到「{args.name}」。")
        else:
            print(f"未找到配置「{args.name}」。运行 `personal-ai config list` 查看。", file=sys.stderr)
            raise SystemExit(1)
        return

    if action == "show":
        p = store.get_active()
        if p is None:
            print("未设置当前 provider。运行 `personal-ai config init`。")
            return
        print(f"名称      : {p.name}")
        print(f"格式      : {p.format}")
        print(f"Base URL  : {p.base_url or '-'}")
        print(f"模型      : {p.model or '-'}")
        print(f"API Key   : {p.masked_key()}")
        print(f"更新于    : {p.updated_at}")
        return

    if action == "edit":
        profile = store.get(args.name)
        if profile is None:
            print(f"未找到配置「{args.name}」。", file=sys.stderr)
            raise SystemExit(1)
        fields: dict = {}
        new_url = _prompt(f"Base URL（当前 {profile.base_url or '-'}）", "")
        if new_url:
            fields["base_url"] = new_url
        new_model = _prompt(f"模型（当前 {profile.model or '-'}）", "")
        if new_model:
            fields["model"] = new_model
        new_key = _prompt_secret("新 API Key（留空保持不变）")
        if new_key:
            fields["api_key"] = new_key
        if fields:
            store.update(args.name, **fields)
            print(f"已更新「{args.name}」。")
        else:
            print("未做任何修改。")
        return

    if action == "remove":
        if store.remove(args.name):
            print(f"已删除「{args.name}」。")
        else:
            print(f"未找到配置「{args.name}」。", file=sys.stderr)
            raise SystemExit(1)
        return

    print(f"未知操作: {action}", file=sys.stderr)
    raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="personal-ai",
        description="Personal AI OS command-line client",
    )
    sub = parser.add_subparsers(dest="command")

    chat = sub.add_parser("chat", help="start an interactive chat session")
    chat.add_argument("--session", help="existing session id (default: create one)")
    chat.add_argument("--no-thinking", action="store_true",
                      help="hide the model's chain of thought (thinking)")
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

    config = sub.add_parser(
        "config",
        help="manage local LLM provider profiles (multi-API switching)",
        description=(
            "Profiles are stored locally outside the repo (secrets never enter git). "
            "Formats: openai (compatible: OpenAI/DeepSeek/Kimi/GLM/Qwen/Ollama) or anthropic."
        ),
    )
    config_sub = config.add_subparsers(dest="action", required=True)
    config_sub.add_parser("init", help="interactive first-run wizard").set_defaults(func=cmd_config)
    config_sub.add_parser("list", help="list saved profiles").set_defaults(func=cmd_config)
    config_sub.add_parser("show", help="show the active profile").set_defaults(func=cmd_config)
    use = config_sub.add_parser("use", help="switch the active profile")
    use.add_argument("name")
    use.set_defaults(func=cmd_config)
    edit = config_sub.add_parser("edit", help="edit a profile (url/model/key)")
    edit.add_argument("name")
    edit.set_defaults(func=cmd_config)
    remove = config_sub.add_parser("remove", help="delete a profile")
    remove.add_argument("name")
    remove.set_defaults(func=cmd_config)

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
