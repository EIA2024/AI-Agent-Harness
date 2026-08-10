# Department 10 — Model Gateway

> **职责**：提供模型无关的 LLM 调用抽象。支持多 Provider 路由、按任务类型选择模型、成本控制与预算管理、Failover 容灾。不绑定任何单一模型厂商。
> **优先级**：P0（Foundation）
> **预估规模**：M
> **负责目录**：`packages/model_gateway/`

---

## 1. 部门边界

### 你负责
- `ModelProvider` Protocol — 统一的模型调用接口
- Provider Adapter 实现（至少支持 OpenAI API compatible + Anthropic）
- Model Router — 按 purpose / latency_class / quality_class 选择模型
- Model Roles — router / worker / vision / embedding 至少四类
- Cost Tracking — 每个 Run 的 token 使用和费用
- Budget Control — run_budget / daily_budget / monthly_budget
- Failover — primary → fallback → safe failure
- 多 Provider 时的 LiteLLM 集成（可选）

### 你不负责
- 什么时候调用模型 → Department 01（Agent Runtime）
- 构建 Prompt/messages → Department 06（Context Engine）
- 哪些 Tool Schema 传给模型 → Department 03 / 06
- Token 计数在 Context Budget 中是总量控制 → Department 06
- Streaming 的 HTTP 传输 → Department 12（API Layer）

### 你的上游依赖
- Context Engine（Department 06）— 组装好的 messages
- Tool System（Department 03）— tools schema

### 你提供给下游
- `ModelProvider.complete(ModelRequest) → ModelResponse`
- `ModelProvider.stream(ModelRequest) → AsyncIterator[ModelStreamEvent]`

---

## 2. 核心数据结构

```python
from pydantic import BaseModel
from typing import Literal

class ModelRequest(BaseModel):
    """发给 Model Gateway 的请求"""

    # Purpose 决定用哪类模型
    purpose: str  # "intent_classification" | "memory_extraction" |
                  # "assistant" | "reasoning" | "vision" | "embedding"

    messages: list[dict]  # [{"role": "system"|"user"|"assistant"|"tool", "content": "..."}]
    tools: list[dict] | None  # Tool schema 列表

    # 质量与延迟约束
    latency_class: Literal["realtime", "normal", "batch"] = "normal"
    quality_class: Literal["low", "medium", "high", "max"] = "medium"

    # 成本约束
    max_cost: float | None  # 单次调用最大花费（美元）

    # 模型选择偏好
    preferred_provider: str | None  # "openai" | "anthropic" | "auto"
    preferred_model: str | None     # 指定具体 model id

    # 生成参数（会被 Model Gateway 根据 purpose 调整）
    temperature: float | None
    max_tokens: int | None

class ModelResponse(BaseModel):
    """模型返回"""

    content: str | None        # 文本回复
    tool_calls: list[dict] | None  # 工具调用列表

    model: str                 # 实际使用的 model id
    provider: str              # 实际使用的 provider

    usage: ModelUsage
    finish_reason: str         # "stop" | "tool_calls" | "length" | "error"

    latency_ms: int

class ModelUsage(BaseModel):
    input_tokens: int
    cached_tokens: int   # 被 prompt cache 命中的 token 数
    output_tokens: int
    total_tokens: int
    cost_usd: float      # 本次调用费用
```

---

## 3. ModelProvider Protocol

```python
from typing import Protocol, AsyncIterator

class ModelStreamEvent(BaseModel):
    type: Literal["text_delta", "tool_call_delta", "done", "error"]
    text: str | None
    tool_call: dict | None
    usage: ModelUsage | None

class ModelProvider(Protocol):
    provider_name: str

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """同步完成（等全部生成完毕再返回）"""
        ...

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """流式生成"""
        ...

    async def list_models(self) -> list[dict]:
        """返回此 Provider 支持的模型列表"""
        ...

    async def health_check(self) -> bool:
        """检查 Provider 是否可用"""
        ...
```

---

## 4. Model Roles（至少四类）

| Role | Purpose | 要求 | 示例模型 |
|------|---------|------|---------|
| `router` | Intent classification, tool selection | 快速、便宜 | Haiku 4.5, GPT-4o-mini |
| `worker` | 常规助理对话、任务执行 | 平衡 | Sonnet 5, GPT-4o |
| `vision` | 截图理解、图片分析 | 多模态 | Sonnet 5, GPT-4o |
| `embedding` | 文本向量化（Memory 检索） | 便宜、向量质量好 | text-embedding-3-small |

未来扩展：
```text
coding     — 复杂代码生成
research   — 深度研究（long context）
memory     — Memory extraction（structured output）
```

---

## 5. Model Router

### 路由策略

```text
Intent classification
    → cheap / fast model（router role）

Memory extraction
    → cheap structured-output model（router role + json_mode）

Normal assistant
    → balanced model（worker role）

Complex reasoning / planning
    → high quality model（worker role, quality_class=max）

Screenshot / image
    → vision model（vision role）

Embedding
    → embedding model（embedding role）
```

### 路由实现

```python
class ModelRouter:
    def __init__(self, providers: dict[str, ModelProvider], config: RouterConfig):
        self.providers = providers
        self.config = config

    async def route(self, request: ModelRequest) -> tuple[ModelProvider, str]:
        """
        根据 request 的 purpose + latency_class + quality_class
        选择最合适的 (provider, model_id)
        """
        ...

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Route → 调用 → 如果失败 try fallback"""
        provider, model = await self.route(request)

        try:
            return await provider.complete(request)
        except RateLimitError:
            # Try next cheapest provider
            fallback_provider = self.get_fallback(provider, request)
            return await fallback_provider.complete(request)
        except Exception as e:
            # Log and raise
            raise ModelGatewayError(f"All providers failed: {e}")
```

### 默认 Provider 配置

```yaml
# model_gateway_config.yaml
providers:
  anthropic:
    api_key_ref: "anthropic_api_key"
    models:
      - id: claude-sonnet-5
        role: worker
        quality_class: high
      - id: claude-haiku-4-5
        role: router
        quality_class: low

  openai:
    api_key_ref: "openai_api_key"
    models:
      - id: gpt-4o
        role: worker
        quality_class: medium
      - id: gpt-4o-mini
        role: router
        quality_class: low
      - id: text-embedding-3-small
        role: embedding

fallback_order:
  - anthropic → openai
  - openai → anthropic
```

---

## 6. Cost Control

### Run 级别跟踪

每个 Run 存储：

```json
{
  "model_usage": {
    "input_tokens": 15000,
    "cached_tokens": 5000,
    "output_tokens": 800,
    "embedding_tokens": 0
  },
  "cost": {
    "model_cost_usd": 0.15,
    "tool_cost_usd": 0,
    "sandbox_cost_usd": 0,
    "total_cost_usd": 0.15
  }
}
```

### Budget 控制

```python
class BudgetTracker:
    async def check_budget(
        self,
        owner_id: UUID,
        estimated_cost: float,
    ) -> bool:
        """
        检查是否超出预算：
        - run_budget: 单次 Run 上限
        - daily_budget: 日上限
        - monthly_budget: 月上限
        如果任何一项超出 → 返回 False，Run 停止
        """
        ...

    async def record_usage(
        self,
        owner_id: UUID,
        run_id: UUID,
        usage: ModelUsage,
    ) -> None:
        """记录使用量"""
        ...
```

### Budget 配置

```yaml
budget:
  run_max_cost_usd: 2.0
  daily_max_cost_usd: 10.0
  monthly_max_cost_usd: 50.0
```

---

## 7. Failover 策略

```text
Primary Model
    ↓ 429 Rate Limit / 5xx Server Error
Fallback Provider（下一个最便宜的 provider）
    ↓ 也失败
Safe Failure（返回错误，不继续 retry）
```

**不要无限 retry** — 最多尝试 2 个 provider，全失败就报错。

---

## 8. LiteLLM 集成（可选）

如果未来需要：
- 多 Provider load balancing
- Provider normalization（统一不同厂商的 API 格式）
- 更细粒度的 budget control

再引入 LiteLLM：

```python
# 可选：用 LiteLLM 替代自写 adapter
import litellm

class LiteLLMProvider(ModelProvider):
    async def complete(self, request: ModelRequest) -> ModelResponse:
        response = await litellm.acompletion(
            model=request.preferred_model or "claude-sonnet-5",
            messages=request.messages,
            tools=request.tools,
            ...
        )
        return self._to_model_response(response)
```

第一版建议自己写 provider adapter（更简单，依赖更少）。

---

## 9. 详细任务列表

### Task 1: 定义核心数据模型
- [ ] `ModelRequest` / `ModelResponse` / `ModelUsage` / `ModelStreamEvent` Pydantic models
- [ ] `ModelProvider` Protocol

### Task 2: 实现 Provider Adapters
- [ ] Anthropic Provider Adapter（Messages API）
- [ ] OpenAI Provider Adapter（Chat Completions API）
- [ ] Provider 健康检查
- [ ] 错误标准化（Rate Limit / Auth Error / Server Error → 统一异常）

### Task 3: 实现 Model Router
- [ ] 按 purpose map 到 role → 选择 model
- [ ] `RouterConfig` 加载（YAML/JSON）
- [ ] Fallback 逻辑（primary → next provider）
- [ ] 失败后不再选择同一个 provider

### Task 4: 实现 Cost Tracking
- [ ] Token 使用量记录
- [ ] 费用计算（按 model 的 input/output/cached 价格）
- [ ] `BudgetTracker` — 检查 run/daily/monthly 预算
- [ ] 超出预算时阻止新的 LLM 调用

### Task 5: 实现 Embedding Provider
- [ ] OpenAI text-embedding-3-small adapter
- [ ] 批量化 embedding（一次请求多条文本）
- [ ] Embedding 维度适配（支持不同 embedding model 的维度）

### Task 6: 测试
- [ ] ModelProvider mock 测试
- [ ] Router 正确路由测试（不同 purpose → 不同 role）
- [ ] Failover 测试（模拟 primary 失败 → fallback 成功）
- [ ] Budget 超限测试
- [ ] Cost 计算准确性测试

---

## 10. 验收标准

### 多 Model 路由测试
```text
Request: purpose="intent_classification", quality_class="low"
    → Router selects: router role model (e.g. Haiku 4.5)

Request: purpose="reasoning", quality_class="max"
    → Router selects: high quality worker model (e.g. Sonnet 5)

Request: purpose="vision"
    → Router selects: vision-capable model
```

### Failover 测试
```text
Primary provider returns 429
    → Router switches to fallback provider
    → Request succeeds
    → Cost is tracked correctly (against the model that actually ran)
```

### Budget 测试
```text
daily_budget = $0.50
Run 1 costs $0.30 → allowed
Run 2 costs $0.30 → rejected (would exceed $0.50 daily budget)
```

---

## 11. 参考蓝图章节

- Section 33: Model Router
- Section 34: Cost Control
- Section 46: Model Interface (ModelProvider Protocol)
- Section 57: Model Router Eval
- Section 83: Tech Stack (LLM Gateway)
