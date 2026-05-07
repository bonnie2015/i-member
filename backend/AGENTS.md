# AGENTS.md

## Agent 开发约束

### 编码原则

- 代码风格清晰、精炼、简洁、可读性高。
- 以业务功能为单位拆解方法，杜绝过度设计。
- 对于复用率高的逻辑可以适当抽象成公共方法。
- 先理解、执行计划，确认所有细节后再行动。
- 所有测试在 Docker 容器内部进行。
- 提示词中不暴露任何品牌或业务细节。

### 基类

所有 agent 继承 `BaseAgent`（`app/agents/base.py`），实现 `_execute(self, input: AgentInput) -> AgentOutput`。不要直接调 `_execute`，用 `agent.run(input)`，基类统一处理超时/异常降级。

### LLM 选择

- **用户可见回复** → `get_remote_llm`（DeepSeek）：推荐、QA、ticket
- **内部判断/分类** → `get_local_llm`（qwen2.5:7b）：router、guard

### ReAct Agent 结束方式

每个 ReAct agent 设 `max_tool_calls` 上限，到达后只暴露结束工具，强制终止。

### Guard 模式

服务类 agent（推荐、ticket）前加 guard：单次 LLM 调用，structured output，判断是否继续/结束，压缩上下文。guard 用本地模型。

### 子图规范

子图结束时必须设 `current_subgraph=None`，让主图下轮走 router 重新意图识别。

### Prompt

Prompt 文件放 `app/prompts/<agent>/`，通过 `prompt_builder.py` 加载和组装。
