# 13 — Configuration / Provider / MCP

## 1. 保留现有 provider profile 概念

当前 provider store 已支持：
- OpenAI-compatible；
- Anthropic；
- DeepSeek；
- Moonshot/Kimi；
- GLM；
- Qwen；
- Ollama。

升级不要把 provider 配置重新塞进 TUI app state 文件。

---

## 2. Config command 统一

```text
config init
config list
config show [name]
config use <name>
config edit <name>
config remove <name>
config path
config validate
```

输出默认 human table，`--json` 机器格式。

---

## 3. Model/provider picker

TUI `/model`：
- 当前 profile 可用 model；
- 最近使用；
- aliases；
- capability badges（仅 API 明确知道时）。

`/provider`：
- profile；
- base URL；
- auth configured yes/no；
- provider kind；
- health。

不显示完整 API key。

---

## 4. Capability normalization
不同 provider 可能支持：
- tool calling；
- reasoning；
- images；
- context window；
- streaming。

建立 `ProviderCapabilities`，UI 只依据 capability，而不是：
```python
if provider_name == "glm": ...
```

---

## 5. Project config
建议未来：
`.personal-ai/config.toml`

可保存：
- default profile alias；
- session/project preferences；
- UI theme；
- tool presentation；
- allowed external context dirs（仅偏好，不是 security grant）。

secret 不进入 repo。

---

## 6. MCP
当前架构主要是 native connector。若未来接 MCP：
- MCP server config 是 tool source；
- 所有 MCP tool 仍进 ToolBroker/Policy；
- CLI `/tools` 可按 source filter；
- `/mcp` 只管理连接，不绕过 broker。

---

## 7. GLM/Kimi 的定位
GLM、Kimi、OpenAI、Anthropic 都是 provider/model layer。
它们不应该改变：
- Session；
- Run；
- Approval；
- Memory；
- Tool event 的 UI 语义。
