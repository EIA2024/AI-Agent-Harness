# Personal AI CLI v2

The CLI is a terminal control plane for the Personal AI OS runtime: interactive
TUI, headless `exec`, and admin commands for sessions / runs / approvals /
memories / tools / audit / config.

```text
personal-ai                        interactive UI (Textual TUI in a TTY;
                                   plain fallback on pipes / NO_COLOR)
personal-ai --continue             resume the most recent session
personal-ai --session <id>         resume a specific session
personal-ai --pick                 choose a session interactively
personal-ai exec "..."             one-shot headless run
personal-ai exec -o json           machine-readable result
personal-ai exec -o stream-json    versioned JSONL event stream
personal-ai sessions list/show
personal-ai runs list/show/cancel/resume
personal-ai approvals list/approve/reject/edit
personal-ai memories list/search/show/forget
personal-ai tools list
personal-ai audit list
personal-ai config init/list/show/use/edit/remove/path/sources/validate
personal-ai doctor [--json]
```

## Headless contract (exec)

| Mode | stdout | stderr |
|---|---|---|
| `text` | final assistant answer only | tool progress, session notice, errors |
| `json` | one result object (`v`,`status`,`output`,`error`) | same as text |
| `stream-json` | JSONL lines, one per event (`{"v":1,"type":...,...}`) | same as text |

stdout is never polluted with spinners/ANSI/banners. Prompt may be `-` (read
stdin) or omitted when stdin is a pipe.

Exit codes: `0` completed · `1` internal · `2` usage/config · `3` auth ·
`4` approval required · `5` run failed · `6` cancelled · `7` network/server ·
`8` protocol/schema.

## Security model

- The server's ToolBroker / Policy Engine are the source of truth for
  permissions; the CLI only displays and posts the user's decision.
- Raw chain-of-thought is never a public CLI surface; legacy `thinking.delta`
  events are accepted for compatibility but their text is discarded.
- All terminal output is sanitized against ANSI/OSC/control-sequence injection
  and sensitive argument values (api_key/token/password/…) are redacted.
- API keys are never printed; `doctor` reports configured/missing only.

## Session & approval UX

- `--continue` / `--session` / `--pick` resume without hand-copying UUIDs.
- When a run pauses for approval the TUI raises an approval modal
  (approve / reject / edit arguments / cancel run); the decision is posted and
  the stream resumes in place, preserving the transcript.
- Notifications (terminal bell) are opt-in via `PERSONAL_AI_NOTIFY=1`.

## Provider setup and hot switching

The TUI reads the API service's runtime Provider status at startup. With no
active Provider it remains usable in Echo demo mode, keeps a visible prompt to
run `/api`, and labels every demo answer `[Echo demo response]`.

- `/api` adds, updates, or activates a local Provider Profile. First-time setup
  defaults to DeepSeek, `https://api.deepseek.com/v1`, and `deepseek-chat`.
- Provider API keys use a masked input and are stored only in the operating
  system Keyring. If Keyring storage is unavailable, setup fails instead of
  writing a plaintext key to `profiles.json`.
- `/model` lists models through the active Provider when supported and falls
  back to a manual model ID when enumeration fails.
- DeepSeek exposes reasoning effort as `auto（由模型决定）`; select
  `deepseek-chat` or `deepseek-reasoner` rather than sending unsupported
  `low` / `medium` / `high` parameters.
- A successful `/api` or `/model` change is validated and atomically reloaded
  into the API runtime. It affects subsequent Runs while preserving the current
  session, transcript, and stored memory. Changes are rejected during an active
  Run.

Hot switching is a local control plane: the TUI and API must point at the same
`PERSONAL_AI_CONFIG_DIR`. A TUI connected to a remote API is told to configure
the server and cannot apply its local profiles remotely. `personal-ai config`
uses the same `ProviderConfigStore`, presets, validation, capability rules, and
Keyring storage, but the command itself only edits local profiles; use the
local TUI `/api` flow to validate and reload an already-running API process.

## Environment

`PERSONAL_AI_API_URL` (default `http://localhost:8000`),
`PERSONAL_AI_API_KEY` (default `dev-key`), `PERSONAL_AI_CONFIG_DIR`
(provider profiles dir, default `~/.personal_ai`), `NO_COLOR`,
`PERSONAL_AI_NOTIFY`, `REDUCED_MOTION`.

## Architecture

```text
personal_ai_os/cli/
├── main.py         Typer root (dispatch only) + legacy aliases
├── legacy.py       pre-v2 argparse CLI (compatibility path)
├── api/            AsyncAPIClient, DTOs, typed errors, SSE decoder
├── domain/         UIEvent normalizer, AppState, reducer
├── commands/       headless exec + admin subcommands
├── controllers/    side-effectful actions shared by TUI and plain mode
├── output/         text / json / JSONL renderers + exit codes
├── tui/            Textual app, widgets, screens, slash commands
├── notify.py       opt-in notifications
└── sanitize.py     terminal output sanitizer + secret redactor
```

The transport/domain layers are shared by the TUI and headless modes; the
presentation layers are separate, so CI never needs a TTY.
