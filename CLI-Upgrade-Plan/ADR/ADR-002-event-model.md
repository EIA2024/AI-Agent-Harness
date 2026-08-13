# ADR-002 — Event Model

## Decision
UI 只消费 `UIEvent`，不直接消费 SSE payload。

## Why
- live/replay 差异；
- API version；
- headless/TUI 共用；
- deterministic tests；
- reconnect/dedupe；
- raw reasoning policy。

## Rule
```text
SSE -> ServerEvent -> normalize -> UIEvent -> reducer
```

## Compatibility
Normalizer 同时支持 legacy 与 envelope v1。
删除 legacy 需要独立版本决策。
