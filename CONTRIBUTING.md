# Contributing

Thanks for considering a contribution! This project is a personal agent
runtime with a strong security posture; please read the
[Architecture](docs/ARCHITECTURE.md) before changing behavior.

## Development setup

```bash
uv sync --extra dev
uv run python -m apps.api.main      # run the API (dev mode)
uv run pytest -v                    # run the test suite
uv run ruff check src connectors apps tests
```

## Design constraints (non-negotiable)

1. **Agent never holds permissions** — every capability call goes through the
   `ToolBroker`. Don't bypass it.
2. **Security boundaries live in code, not prompts** — the broker re-verifies
   approvals; trust labels isolate external content.
3. **Cross-module dependencies are injected** — add a Protocol in
   `common/protocols.py`, never import another module's implementation.
4. **Secrets never enter the repo** — API keys live in
   `~/.personal_ai/profiles.json` (gitignored). Test with fake keys only.
5. **Prompt is not a security boundary.**

## Testing expectations

- Every module has unit tests under `tests/unit/` (SQLite in-memory).
- Integration paths live in `tests/integration/test_vertical_slice.py`.
- Keep the full suite green: `uv run pytest` and `ruff` clean.
- New connectors must pass SSRF / path-jail / injection adversarial checks.

## Commit style

- Concise, imperative subject line; describe *why* when non-obvious.
- Reference the module affected (e.g. `fix(tool_broker): …`).
