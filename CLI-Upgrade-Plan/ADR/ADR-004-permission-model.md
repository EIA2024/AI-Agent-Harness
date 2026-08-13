# ADR-004 — Permission Model in CLI

## Decision
CLI 不实现自己的授权策略。

## Current
Server ToolBroker/Policy Engine/R0-R4/ApprovalEngine 是真源。

## Implications
- P0 不能通过自动 POST approve 实现 yolo；
- approve/edit/reject 都走 API；
- future approval mode 必须 server-side；
- audit 必须区分 human approval 与 policy auto allow。
