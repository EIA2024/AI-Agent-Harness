# 02 — 第一性原理：Personal Agent CLI 应该解决什么

## 1. CLI 的本质不是“终端版聊天框”

对一个能够记忆、调用工具、等待审批、恢复 Run 的 Agent，用户在每一时刻需要回答 4 个问题：

1. **State — 它现在处于什么状态？**
2. **Evidence — 它刚刚观察/改变了什么？**
3. **Control — 我此刻能对它做什么？**
4. **Boundary — 它当前被允许做什么、带着什么上下文？**

设计中每一个组件都必须至少服务于其中一个问题。

---

## 2. 状态机优先于 token 流

错误模型：

```text
LLM token -> print token -> print tool -> print answer
```

正确模型：

```text
Server Event
   ↓
Transport
   ↓
Normalized UI Event
   ↓
Reducer
   ↓
App State
   ↓
State-aware Presentation
```

原因：
- Agent 会暂停；
- Agent 会 resume；
- 事件可能 replay；
- tool 可能长时间 running；
- approval 是独立状态；
- terminal resize 会重绘；
- headless 与 TUI 需要同一语义。

---

## 3. 终端的信息预算有限

终端宽度通常 60–160 列，且高度经常 < 40 行。

因此使用 **progressive disclosure**：

默认显示：
- tool 名称 + 一行 intent/result；
- diff summary；
- approval risk + action；
- session/model/mode；
- final answer。

按键展开：
- full tool args；
- stdout/stderr；
- full diff；
- full metadata；
- debug trace。

不要默认把 JSON、UUID、token usage、完整 tool output 铺满 transcript。

---

## 4. 线性 transcript 比永久 dashboard 更适合 Agent

Agent 的工作天然是时间序列：
`user -> reasoning summary -> tool -> observation -> approval -> result -> answer`

默认布局保持线性，辅以 overlay/picker。

这样：
- 更适合终端 scrollback；
- 更容易复制；
- 窄屏自然退化；
- 用户能理解因果顺序；
- 与 headless transcript 同源。

宽屏可增加临时 detail pane，但不能成为完成关键操作的唯一入口。

---

## 5. 可脚本化输出是产品接口

当 CLI 被 shell/CI 调用：
- stdout = 结果协议；
- stderr = 人类可读进度/诊断；
- exit code = 状态；
- JSON/JSONL = 稳定 schema。

因此 TUI 不能成为核心逻辑的宿主。

---

## 6. Approval 是“控制权交接”

审批不是弹一个 Y/N。

一个合格 approval UI 要说明：
- Agent 想做什么；
- 使用哪个 tool；
- 风险等级；
- 关键参数；
- 潜在 side effect；
- 哪些数据会离开机器；
- 是否可编辑参数；
- approve 的范围仅本次还是更广；
- reject 后 Agent 是继续还是 run 结束。

本项目已有 R0–R4 和 exact-args hash binding，应直接成为 UX 的产品优势。

---

## 7. Memory 可见，但不能喧宾夺主

长期记忆系统需要信任。

CLI 应支持：
- 查看本轮使用了哪些 memory 的摘要；
- 搜索/编辑/forget；
- 展示 compaction 发生；
- 明确 temporary conversation context 与 persisted memory 的差别。

但默认 transcript 不逐条倾倒 memory 内容。

---

## 8. 不显示 raw chain-of-thought

当前服务端存在 `thinking.delta`。CLI v2 的默认产品行为：
- 不将 raw chain-of-thought 当用户功能；
- 使用明确、安全的状态提示（例如 “正在检索相关记忆”“正在执行 filesystem.read”）；
- 若后端未来提供 `reasoning.summary`，展示摘要；
- raw reasoning 只作为受控内部 debug 数据，并需单独安全审查。

这既降低信息噪声，也避免把内部推理当成可靠解释。

---

## 9. “快速”来自低认知延迟，而不只是低网络延迟

优化顺序：
1. 用户输入后立即有状态反馈；
2. tool 立即显示 started；
3. 长 tool 有 elapsed/progress；
4. approval 立即抢占焦点；
5. final response 流式；
6. 后台 metadata 延迟加载。

不要在启动时同步检查所有 provider/tool/DB 才显示 UI。

---

## 10. CLI 的差异化

不复制 coding CLI 的全部能力。

本项目最值得突出：
- persistent identity/session；
- memory；
- policy；
- approval；
- audit；
- automation（未来）；
- multi-provider。

因此产品定位可概括为：

> **A terminal control plane for a persistent personal agent runtime.**
