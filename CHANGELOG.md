# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/), and this
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-08-11

Initial MVP release — a runnable Personal AI OS vertical slice.

### Added

- **Agent Runtime** — LangGraph state machine (intake → context → decide →
  tool → respond → memory), task classification (L0–L4), HITL approval with
  argument-hash binding, durable run persistence, SSE live streaming.
- **Tool System** — `ToolRegistry` + `ToolBroker` (schema validation, policy,
  credential injection, timeout, result sanitization); built-in connectors:
  calculator (safe `ast` eval), filesystem (path-jail + sensitive-file
  protection), http_fetch (SSRF guard + HTML→text).
- **Security** — R0–R4 policy engine, approval engine with exact-argument
  binding, credential broker with secret redaction, trust labels, unbounded
  tool-loop guard.
- **Memory** — SQL store with provenance, hybrid retrieval (cosine + keyword +
  importance + recency), rule-based preference extraction.
- **Context Engineering** — 4-tier prompt assembly, token budgets, trust
  isolation, **MemGPT-style conversation compaction** (summary + recent window).
- **Model Gateway** — Anthropic + OpenAI-compatible (DeepSeek/Kimi/GLM/Qwen)
  providers, model router with failover, cost tracking, **live streaming**
  with chain-of-thought (reasoning_content) surfaced.
- **Provider profiles** — multiple local LLM configs (`~/.personal_ai/profiles.json`,
  outside the repo), interactive `config init` wizard, one-command switching.
- **API** — FastAPI REST + SSE streaming, owner-scoped, CLI client with
  Codex/Kimi-style streaming UX.
- **Deployment** — Docker Compose (api + pgvector postgres), Alembic scaffolding.

### Fixed

- DeepSeek reasoning-model compatibility: tool-name sanitization, reasoning
  content replay, tool-message adjacency.
- Cross-dialect (SQLite/PostgreSQL) FK cycle and timezone handling.
- Excessively long chain-of-thought on simple questions (complexity-aware
  reasoning directive).

### Security

- No secrets in the repository. Provider API keys live in `~/.personal_ai`
  (outside the repo). Real key patterns are gitignored.
