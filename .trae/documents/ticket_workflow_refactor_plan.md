# Ticket Workflow 重构计划

## 目标
将现有的 `ticket_node` 单节点实现改造为完整的 LangGraph workflow，采用 plan-executor-reflect 三阶段机制，每个阶段都支持 interrupt，并能正确恢复执行。

## 当前问题分析

### 1. 现有实现的问题
- 所有逻辑集中在单个 `ticket_node` 函数中
- 使用循环处理追问，逻辑复杂难以维护
- interrupt 恢复后重新执行整个节点，效率低
- 状态管理不够清晰

### 2. 需要解决的核心问题
- 如何将 plan-executor-reflect 拆分为独立节点
- 每个节点如何支持 interrupt
- 中断后如何恢复到正确的节点继续执行
- 状态如何在节点间传递

## 架构设计

### Workflow 结构
```
[entry] -> [plan_node] -> [executor_node] -> [reflect_node] -> [exit]
              |                |                 |
           interrupt       interrupt         interrupt
              |                |                 |
           resume          resume            resume
```

### 节点职责

#### 1. plan_node（规划节点）
**职责**：
- 分析用户需求，制定执行计划
- 判断是否需要更多信息
- 如果需要，通过 interrupt 向用户提问

**输入状态**：
- `messages`: 对话历史
- `user_id`: 用户ID
- `skill_names`: 已识别的技能列表

**输出状态**：
- `plan`: PlanOutput 对象
- `messages`: 更新后的对话历史（包含追问和回答）

**Interrupt 场景**：
- 信息不足时需要追问用户
- 追问后恢复，继续生成完整计划

#### 2. executor_node（执行节点）
**职责**：
- 执行 plan 中的工具调用
- 收集执行结果
- 处理执行过程中的异常

**输入状态**：
- `plan`: PlanOutput 对象
- `user_id`: 用户ID

**输出状态**：
- `execution_results`: 执行结果列表
- `execution_status`: 执行状态（成功/部分成功/失败）

**Interrupt 场景**：
- 需要用户确认的操作（如写操作）
- 执行失败需要用户决策时

#### 3. reflect_node（反思节点）
**职责**：
- 分析执行结果
- 判断是否满足用户需求
- 生成最终回复或决定是否需要重新规划

**输入状态**：
- `plan`: PlanOutput 对象
- `execution_results`: 执行结果
- `messages`: 对话历史

**输出状态**：
- `final_reply`: 最终回复
- `need_replan`: 是否需要重新规划
- `reflection`: ReflectOutput 对象

**Interrupt 场景**：
- 执行结果不满足需求，需要用户补充信息
- 需要用户确认下一步操作

### 状态设计（AgentState 扩展）

```python
class AgentState(TypedDict):
    # 基础信息
    user_id: str
    thread_id: str
    channel: str
    
    # 对话消息
    messages: Annotated[List[BaseMessage], add_messages]
    final_reply: Optional[str]
    
    # Plan 阶段
    plan: Optional[PlanOutput]
    plan_attempt_count: int  # 规划尝试次数，防止无限循环
    
    # Executor 阶段
    execution_results: Optional[List[Dict[str, Any]]]
    execution_status: Optional[str]  # "success" | "partial" | "failed"
    current_step_index: int  # 当前执行到的步骤索引
    
    # Reflect 阶段
    reflection: Optional[ReflectOutput]
    need_replan: bool
    replan_count: int  # 重新规划次数，防止无限循环
    
    # 中断恢复相关
    interrupt_node: Optional[str]  # 哪个节点被中断了
    interrupt_question: Optional[str]  # 中断时的问题
    interrupt_context: Optional[Dict]  # 中断时的上下文
```

## 实现步骤

### Phase 1: 状态定义扩展
1. 在 `state.py` 中添加 workflow 所需的字段
2. 确保所有字段都有合适的默认值
3. 添加必要的 reducer 函数

### Phase 2: 节点函数实现
1. **实现 plan_node**
   - 提取现有 `_plan` 逻辑
   - 添加 interrupt 支持
   - 处理追问循环

2. **实现 executor_node**
   - 提取现有 `_execute` 逻辑
   - 支持分步执行和状态保存
   - 添加需要确认时的 interrupt

3. **实现 reflect_node**
   - 提取现有 `_reflect` 逻辑
   - 支持不满意时的 replan 决策
   - 添加需要补充信息时的 interrupt

### Phase 3: Workflow 图构建
1. 创建 `ticket_workflow.py` 文件
2. 定义节点间的路由逻辑
3. 构建完整的 workflow 图
4. 配置 checkpointer 支持中断恢复

### Phase 4: 集成到主图
1. 修改 `graph.py` 中的 `ticket_block` 调用
2. 确保状态正确传递
3. 测试中断和恢复流程

### Phase 5: 测试验证
1. 测试正常流程
2. 测试各节点的 interrupt 场景
3. 测试中断后的恢复
4. 测试边界情况（如多次中断、循环等）

## 关键技术点

### 1. 中断恢复机制
```python
# 在节点中检查是否需要中断
if need_more_info:
    # 保存当前上下文
    state["interrupt_node"] = "plan_node"
    state["interrupt_context"] = {...}
    
    # 触发中断
    user_answer = interrupt(question)
    
    # 恢复后继续执行
    state["messages"].append(HumanMessage(content=user_answer))
```

### 2. 节点路由逻辑
```python
def route_after_plan(state: AgentState) -> str:
    """根据 plan 结果决定下一步"""
    if state.get("need_replan"):
        return "plan_node"  # 重新规划
    return "executor_node"  # 继续执行

def route_after_executor(state: AgentState) -> str:
    """根据执行结果决定下一步"""
    if state.get("execution_status") == "failed":
        return "plan_node"  # 执行失败，重新规划
    return "reflect_node"  # 继续反思

def route_after_reflect(state: AgentState) -> str:
    """根据反思结果决定下一步"""
    if state.get("need_replan"):
        return "plan_node"  # 需要重新规划
    return END  # 结束
```

### 3. 状态持久化
- 使用 `MemorySaver` 或 `PostgresSaver` 保存状态
- 确保每个节点执行后状态都被正确保存
- 中断时状态自动保存，恢复时自动加载

## 文件变更清单

### 修改文件
1. `harness/workflow/state.py` - 扩展 AgentState
2. `harness/workflow/graph.py` - 集成新的 workflow
3. `harness/agents/ticket.py` - 重构为节点函数

### 新建文件
1. `harness/agents/ticket_workflow.py` - workflow 定义
2. `harness/agents/ticket_nodes.py` - 节点函数实现（可选，如果从 ticket.py 分离）

## 风险评估

### 主要风险
1. **状态兼容性问题**：扩展后的 AgentState 需要与现有代码兼容
2. **中断恢复复杂性**：多节点中断恢复逻辑复杂，容易出错
3. **性能影响**：workflow 比单节点有更多开销

### 缓解措施
1. 保持向后兼容，新字段都有默认值
2. 充分测试各种中断场景
3. 使用异步执行，避免阻塞
4. 添加详细的日志记录，便于调试

## 时间估算
- Phase 1: 30分钟
- Phase 2: 2小时
- Phase 3: 1.5小时
- Phase 4: 30分钟
- Phase 5: 1小时

总计：约 5.5 小时
