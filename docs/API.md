# Personal AI OS — API Reference

All endpoints are under the FastAPI app (`apps.api.main:create_app`). Interactive
docs: `GET /docs` (Swagger UI) when the server is running.

**Authentication** — every request must send an `X-API-Key` header. The server
compares its HMAC digest with `users.api_key_hash`; raw keys are not stored for
new users. When `PERSONAL_AI_DEV_API_KEY` is explicitly set on startup, a dev
`owner` user is created with that key. A missing or unknown key → `401`.

**Ownership** — all resources are scoped to the authenticated user
(`owner_id`); cross-user access returns `404`.

---

## Sessions

| Method | Path | Description |
|---|---|---|
| POST | `/v1/sessions` | create a session (body: `{project_id?, title?, channel?}`) |
| GET | `/v1/sessions` | list the caller's sessions |
| GET | `/v1/sessions/{id}` | session detail |
| PATCH | `/v1/sessions/{id}` | update title / project / status |
| DELETE | `/v1/sessions/{id}` | archive a session |

## Messages

| Method | Path | Description |
|---|---|---|
| POST | `/v1/sessions/{id}/messages` | send a message → starts a run; returns `{run_id, status, message_id}` immediately (streaming) |
| GET | `/v1/sessions/{id}/messages` | message history |

## Runs

| Method | Path | Description |
|---|---|---|
| GET | `/v1/runs/{id}` | run detail: status, public state (`final_response` etc.), steps, tool_calls; internal cached context/reasoning is omitted |
| POST | `/v1/runs/{id}/cancel` | cancel a run (rejects pending approvals) |
| POST | `/v1/runs/{id}/resume` | resume an approval-paused run; body `{approval_id?, decision?, edited_arguments?}` |
| GET | `/v1/runs/{id}/stream` | SSE event stream (live during execution, DB replay after) |

### SSE events

```
event: run.started        data: {run_id, status}
event: text.delta         data: {text}            # streamed answer
event: tool.requested     data: {tool_name, tool_call}
event: run.completed      data: {run_id, status}
event: run.failed / approval.required / run.cancelled
```

## Provider

These authenticated endpoints manage the Provider loaded by the running API.
They are intended for a local TUI and API process that share the same
`PERSONAL_AI_CONFIG_DIR`; write operations reject a mismatched `config_dir`.
Provider API keys remain in the OS Keyring and never appear in responses.

| Method | Path | Description |
|---|---|---|
| GET | `/v1/provider` | active runtime mode, profile, provider, model, reasoning effort, and public capabilities |
| POST | `/v1/provider/reload` | validate and atomically reload the active local Profile; body `{config_dir}` |
| GET | `/v1/provider/models` | list models exposed by the active Provider, when supported |
| PUT | `/v1/provider/model` | update model/reasoning effort and reload; body `{config_dir, model, reasoning_effort}` |

Reload and model changes are rejected while a Run is active. A successful
change affects only subsequently started Runs and preserves sessions,
transcripts, and stored memory.

## Memories

| Method | Path | Description |
|---|---|---|
| GET | `/v1/memories` | list (filter: `type`, `scope`, `status`) |
| POST | `/v1/memories` | create a memory |
| GET | `/v1/memories/{id}` | single memory |
| PATCH | `/v1/memories/{id}` | edit |
| DELETE | `/v1/memories/{id}` | forget (soft delete + audit) |
| POST | `/v1/memories/search` | hybrid search; body `{query, limit?, scope?}` |

## Tools

| Method | Path | Description |
|---|---|---|
| GET | `/v1/tools` | list registered tools (descriptors) |

## Approvals

| Method | Path | Description |
|---|---|---|
| GET | `/v1/approvals` | list pending approvals |
| POST | `/v1/approvals/{id}/approve` | approve |
| POST | `/v1/approvals/{id}/reject` | reject |
| POST | `/v1/approvals/{id}/edit` | approve with edited arguments |

## Automations

| Method | Path | Description |
|---|---|---|
| GET / POST / PATCH / DELETE | `/v1/automations...` | automation CRUD (execution returns 501 until the scheduler is wired) |

## Audit

| Method | Path | Description |
|---|---|---|
| GET | `/v1/audit` | recent audit events (`limit` query, ≤ 500) |

## System

| Method | Path | Description |
|---|---|---|
| GET | `/healthz` | liveness |
| GET | `/readyz` | dependency readiness |

---

## Example: streaming chat

```
POST /v1/sessions/{sid}/messages            {"text": "1+1=?"}
→ 201 {"run_id": "…", "status": "running"}

GET  /v1/runs/{rid}/stream                 (SSE)
→ run.started → tool.requested
→ text.delta* → run.completed
```
