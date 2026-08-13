# CLI v2 — Baseline Inventory (T00)

> Snapshot of the pre-upgrade CLI surface, captured 2026-08-12 for the CLI v2
> upgrade (see `CLI-Upgrade-Plan/`). Records behavior, not intent, so the v2
> refactor has a stable contract to preserve.

## 1. Entry points & packaging

| Item | Value |
|---|---|
| Invocation | `uv run python -m apps.cli.main ...` (repo root; needs `PYTHONPATH` with root + `src`) |
| Console script | none (no `[project.scripts]`; `personal-ai` did not exist) |
| Module package | `apps/cli/` — NOT included in the built wheel (wheel packages only `src/personal_ai_os`) |
| Wheel target | `packages = ["src/personal_ai_os"]` (hatchling) |
| Python | `>=3.12`; dev venv in repo is Python 3.13.1 |

## 2. CLI commands / flags / exit codes

Legacy argparse tree (`apps/cli/main.py`, `build_parser`):

| Command | Args / flags | Behavior |
|---|---|---|
| `chat` | `--session ID`, `--no-thinking` | interactive `input("> ")` loop; POST message; SSE stream render |
| `send TEXT` | `--session ID` | single message; non-streaming fetch of final reply |
| `sessions list` | — | `GET /v1/sessions` → table `id  status  title[:40]` |
| `runs list` | `--limit N` (default 10) | iterate sessions' `active_run_id`, `GET /v1/runs/{id}` each |
| `runs get ID` | positional | print full run JSON (indent 2) |
| `memories search Q` | `--limit N` (10) | `POST /v1/memories/search` |
| `memories list` | — | `GET /v1/memories` |
| `memories forget ID` | positional | `DELETE /v1/memories/{id}` |
| `approve --list` | flag | `GET /v1/approvals?status=pending` |
| `approve ID` | optional positional | `POST /v1/approvals/{id}/approve` |
| `config init` | — | interactive wizard (format → provider → base_url/model → name → API key) |
| `config list` | — | table of saved provider profiles |
| `config show` | — | active profile details |
| `config use NAME` | positional | set active profile |
| `config edit NAME` | positional | prompt url/model/key |
| `config remove NAME` | positional | delete profile |

Exit codes: `0` success/help; `1` API error or wizard-aborted; `2` usage error;
`130` Ctrl+C / EOF during prompt. API errors print `Error <status>: <detail>` to
stderr then `SystemExit(1)` (from `APIClient.request`).

Environment: `PERSONAL_AI_API_URL` (default `http://localhost:8000`),
`PERSONAL_AI_API_KEY` (default `dev-key`), `PERSONAL_AI_CONFIG_DIR` (profiles dir,
default `~/.personal_ai`). Server-side env includes `PERSONAL_AI_DEV_API_KEY`,
`ANTHROPIC_API_KEY`, `DATABASE_URL`, `API_HOST`, `API_PORT`.

## 3. SSE stream contract (as of baseline)

`GET /v1/runs/{id}/stream` (sse-starlette, `text/event-stream`, `: ping` comments
every 15s, no `id:`/`retry:` fields).

Live (run executing; single-consumer per-run queue):
- `run.started` `{run_id}`
- `thinking.delta` `{text}` (dimmed gray)
- `text.delta` `{text}`
- `tool.requested` `{tool_name, tool_call}` — raw LLM tool-call dict
- terminal event `run.completed|run.failed|run.cancelled|approval.required` `{run_id, status}`; stream closes

Replay (run finished / paused, from `RunStep` rows):
- `run.started` `{run_id, status}`
- `thinking.delta` `{text, replay: true}` (from `state["thinking"]`)
- one event per persisted step: `tool.completed|tool.failed|tool.started` (only for
  a `step_type=="tool"`, effectively never persisted) or raw node-name events
  (`intake`, `build_context`, `plan`, `tool_request`, `approval`, `execute`,
  `observe`, `reflect`, `memory_commit`) with data `{run_id, step_id, step_type, status}`
- `text.delta` `{text, replay: true}` from `state["final_response"]` when completed
- terminal event with `{run_id}` only (no `status`)

Client-side hazards: hand-written parser (single-line `data:` only, no multiline),
`tool.completed`/`tool.failed` silent, raw thinking shown by default, fallback to
plain fetch on any stream exception.

## 4. REST API surface (used by CLI)

Auth: `X-API-Key` header → `resolve_user`; owner-scoped (cross-user = 404).

| Endpoint | Purpose |
|---|---|
| `POST /v1/sessions` `{channel, project_id?, title?}` | create session → `{id}` |
| `GET /v1/sessions?status&limit&offset` | list (ordered `last_active_at desc`) |
| `GET /v1/sessions/{id}` | detail incl. `messages[]` |
| `POST /v1/sessions/{id}/messages` `{text}` | send message → `{run_id, status, message_id}` (run creation) |
| `GET /v1/runs/{id}` | run detail incl. `steps[]`, `tool_calls[]`; `state.final_response` |
| `POST /v1/runs/{id}/cancel` | cancel run |
| `POST /v1/runs/{id}/resume` `{approval_id?, decision?, edited_arguments?}` | resume waiting run |
| `GET /v1/runs/{id}/stream` | SSE stream |
| `GET /v1/memories?type&scope&status` / `POST /v1/memories/search` | memory list/search |
| `POST /v1/memories` / `PATCH|DELETE /v1/memories/{id}` | memory CRUD |
| `GET /v1/tools` | registered tool descriptors |
| `GET /v1/approvals?status` | list approvals (DTO: `risk_level`, `arguments_preview`, `argument_hash`, ...) |
| `POST /v1/approvals/{id}/approve\|reject` | resolve approval |
| `POST /v1/approvals/{id}/edit` `{edited_arguments}` | edit + approve |
| `GET /v1/audit?limit` | audit log |
| `GET /v1/automations` ... | automation CRUD (run = 501) |
| `GET /healthz` | health |

There is **no `GET /v1/runs` list** endpoint and no dedicated run-create endpoint
(run creation is implicit via message POST) — v2 Phase 4 (T40) adds the list.

## 5. Configuration store

`src/personal_ai_os/model_gateway/config.py`: `ProviderConfigStore` persists
`{active, profiles}` JSON at `$PERSONAL_AI_CONFIG_DIR/profiles.json` or
`~/.personal_ai/profiles.json` (outside the repo; secrets never in git). Formats:
`openai` / `anthropic`. `KNOWN_PROVIDERS`: openai, anthropic, deepseek, moonshot,
zhipu, qwen, ollama.

## 6. Test baseline

299 tests collected and passing (Python 3.13.1, `uv sync --extra dev`); ruff clean.
CLI coverage: only `tests/unit/test_cli_help.py` (3 subprocess help smoke tests).
No CLI unit tests for `APIClient`, SSE parsing, or command behavior.

## 7. Key v2 constraints derived from this baseline

1. `personal-ai` must become a real console script → CLI code must live in
   `src/personal_ai_os/cli/` (inside the wheel). `apps/cli/main.py` becomes a shim.
2. Old commands keep working (aliases / deprecation), exit codes preserved where
   scripts depend on them.
3. SSE client must handle multiline `data:`, comments, unexpected EOF, and the
   live/replay asymmetry; raw thinking must not be a default product surface.
4. API client must stop printing / `SystemExit` — typed errors instead.
