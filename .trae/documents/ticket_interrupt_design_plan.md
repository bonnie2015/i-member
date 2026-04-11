# Ticket Graph Interrupt 机制设计方案

## 问题背景

当前项目中，ticket\_graph 在 plan 和 executor 环节都有可能产生追问，需要利用 LangGraph 的 interrupt 机制实现挂起和恢复。

核心问题：**在哪里实现中断逻辑？**

* 方案A：直接在 plan 和 executor agent 节点内部实现判断和中断

* 方案B：新加一个"等待用户回复"的专用节点来中断

## 方案对比分析

### 方案A：在 Agent 节点内部实现中断

**实现方式：**

```python
async def planner(state: AgentState):
    # 生成计划
    plan = await agent.plan(...)
    
    # 检查是否需要更多信息
    if plan.need_more_info:
        # 直接在当前节点中断
        user_answer = interrupt(plan.clarify_question)
        # 恢复后继续执行
        messages.append(HumanMessage(content=user_answer))
        # 重新生成计划
        plan = await agent.plan(...)
    
    return {"plan": plan, ...}
```

**优点：**

1. **逻辑内聚**：中断逻辑与业务逻辑紧密耦合，代码直观易懂
2. **状态连续**：中断前后的状态在同一个函数内，便于管理和调试
3. **实现简单**：不需要额外的节点和边，减少图的复杂度
4. **符合直觉**：plan 节点负责"规划"，包括"追问-回复-再规划"的完整流程

**缺点：**

1. **节点职责不单一**：一个节点既做规划又做交互，违反单一职责原则
2. **难以复用**：如果其他场景也需要追问机制，需要重复实现
3. **测试困难**：中断逻辑与业务逻辑混合，单元测试复杂

### 方案B：专用"等待用户回复"节点

**实现方式：**

```python
async def planner(state: AgentState):
    # 生成计划
    plan = await agent.plan(...)
    
    # 只返回计划，不处理中断
    return {
        "plan": plan,
        "need_interrupt": plan.need_more_info,
        "interrupt_question": plan.clarify_question if plan.need_more_info else None
    }

def should_interrupt(state: AgentState):
    # 路由决策：是否需要中断
    if state.get("need_interrupt"):
        return "wait_for_user"
    return "executor"

async def wait_for_user(state: AgentState):
    # 专用节点：等待用户回复
    question = state.get("interrupt_question")
    user_answer = interrupt(question)
    
    return {
        "messages": [HumanMessage(content=user_answer)],
        "need_interrupt": False
    }

# 图结构
workflow.add_node("planner", planner)
workflow.add_node("wait_for_user", wait_for_user)
workflow.add_node("executor", executor)

workflow.add_conditional_edges(
    "planner",
    should_interrupt,
    {
        "wait_for_user": "wait_for_user",
        "executor": "executor"
    }
)
workflow.add_edge("wait_for_user", "planner")  # 回到 planner 重新规划
```

**优点：**

1. **职责分离**：每个节点只做一件事，符合单一职责原则
2. **易于复用**：wait\_for\_user 节点可以被多个场景复用
3. **测试友好**：每个节点可以独立测试
4. **可视化清晰**：图结构明确展示了中断流程

**缺点：**

1. **实现复杂**：需要额外的节点、路由函数和边
2. **状态传递复杂**：需要在节点间传递中断标志和问题
3. **循环处理**：需要处理 planner -> wait\_for\_user -> planner 的循环
4. **学习成本高**：新开发者需要理解额外的抽象层

## 推荐方案

**推荐方案A（在 Agent 节点内部实现中断）**，理由如下：

### 1. 业务逻辑内聚性

plan 和 executor 的中断是**业务逻辑的一部分**，不是通用的交互模式：

* plan 的中断是为了"澄清需求"，然后"继续规划"

* executor 的中断是为了"确认操作"，然后"继续执行"

这些中断是**特定于业务**的，不是通用的"等待用户输入"模式。

### 2. LangGraph 的设计哲学

LangGraph 的 interrupt 机制设计初衷就是**在节点内部使用**，让开发者可以：

* 在任意节点暂停执行

* 恢复时继续执行同一节点

* 保持状态连续性

这与方案A的实现方式完全吻合。

### 3. 状态管理简化

方案A的状态管理更直观：

```python
# 方案A：状态在同一个函数内
async def planner(state):
    plan = await generate_plan(state)
    if plan.need_more_info:
        answer = interrupt(plan.question)  # 中断
        plan = await regenerate_plan(state, answer)  # 恢复后继续
    return plan
```

方案B需要在多个节点间传递状态：

```python
# 方案B：状态在多个节点间传递
async def planner(state):
    plan = await generate_plan(state)
    return {"need_interrupt": plan.need_more_info, "question": plan.question}

async def wait_for_user(state):
    answer = interrupt(state["question"])
    return {"answer": answer}

async def planner_again(state):
    plan = await regenerate_plan(state, state["answer"])
    return plan
```

### 4. 异常处理更简单

方案A可以在同一个 try-except 块中处理所有异常：

```python
async def planner(state):
    try:
        plan = await generate_plan(state)
        if plan.need_more_info:
            answer = interrupt(plan.question)
            plan = await regenerate_plan(state, answer)
        return plan
    except Exception as e:
        # 统一处理所有异常
        logger.error(f"Plan failed: {e}")
        return {"error": str(e)}
```

### 5. 实际案例参考

查看 LangGraph 官方文档和示例，interrupt 都是在节点内部使用的：

* 人机交互（Human-in-the-loop）示例

* 审批流程示例

* 动态规划示例

这些示例都采用了**节点内部中断**的模式。

## 实施计划

### 阶段1：完善 planner 节点的中断机制

1. **修改 ticket\_planer.py**

   * 在 plan 方法中检查 need\_more\_info

   * 使用 interrupt 触发中断

   * 恢复后重新生成计划

2. **修改 workflow/node/ticket\_planer.py**

   * 调用更新后的 plan 方法

   * 正确处理中断和恢复

### 阶段2：实现 executor 节点的中断机制

1. **创建 ticket\_executor.py**

   * 实现独立的 TicketExecutorAgent

   * 支持执行前的确认中断

2. **创建 workflow/node/ticket\_executor.py**

   * 实现 executor 节点

   * 处理执行过程中的中断

### 阶段3：完善路由和状态管理

1. **更新 state.py**

   * 添加中断相关的状态字段

   * 支持中断恢复后的状态重建

2. **更新 graph.py**

   * 集成新的 planner 和 executor 节点

   * 确保子图正确继承父图的 checkpointer

### 阶段4：测试和验证

1. **单元测试**

   * 测试 planner 的中断和恢复

   * 测试 executor 的中断和恢复

2. **集成测试**

   * 测试完整的 plan-executor-reflect 流程

   * 测试多次中断和恢复

## 具体实现示例

### planner 节点实现

```python
# harness/workflow/node/ticket_planer.py

async def planner(state: MainAgentState) -> Dict[str, Any]:
    """
    Planner 节点：制定执行计划
    支持中断追问和恢复
    """
    logger.info("[planner] Starting planning phase")
    
    agent = get_ticket_planner_agent()
    user_id = state.get("user_id", "unknown")
    messages = list(state.get("messages", []))
    
    # 生成计划
    plan = await agent.plan(messages, user_id, skill_names=[])
    logger.info(f"[planner] Generated plan: {plan.target}")
    
    # 检查是否需要更多信息
    if plan.need_more_info:
        question = plan.clarify_question or "请问您能提供更多详情吗？"
        logger.info(f"[planner] Interrupting for more info: {question}")
        
        # 触发中断 - 等待用户回复
        user_answer = interrupt(question)
        
        # 恢复后继续
        messages.append(HumanMessage(content=user_answer))
        
        # 重新生成计划
        plan = await agent.plan(messages, user_id, skill_names=[])
        logger.info(f"[planner] Regenerated plan after clarification: {plan.target}")
    
    # 检查是否需要加载技能详情
    involved_skills = list({s.skill for s in plan.steps if s.skill})
    if involved_skills:
        logger.info(f"[planner] Refining plan with skills: {involved_skills}")
        plan = await agent.plan(messages, user_id, skill_names=involved_skills)
    
    # 转换为新的状态格式
    plan_list = [f"Step {s.step}: {s.tool} - {s.purpose}" for s in plan.steps]
    
    return {
        "plan": plan_list,
        "plan_details": plan.model_dump() if hasattr(plan, 'model_dump') else plan.__dict__,
        "messages": messages,
        "past_steps": [],
        "retry_count": 0,
    }
```

### executor 节点实现

```python
# harness/workflow/node/ticket_executor.py

async def executor(state: MainAgentState) -> Dict[str, Any]:
    """
    Executor 节点：执行工具调用
    支持执行前的确认中断
    """
    logger.info("[executor] Starting execution phase")
    
    agent = get_ticket_executor_agent()
    plan_details = state.get("plan_details")
    
    if not plan_details:
        logger.error("[executor] No plan details found in state")
        return {
            "past_steps": [("执行失败", "未找到执行计划")],
            "execution_status": "failed",
        }
    
    # 检查是否需要用户确认（写操作）
    if plan_details.get("need_confirm"):
        confirm_question = f"我将执行以下操作：{plan_details.get('target')}，请确认是否继续？"
        
        user_answer = interrupt(confirm_question)
        
        # 简单处理：如果用户回答包含"不"或"否"，则取消
        if "不" in user_answer or "否" in user_answer:
            logger.info("[executor] User cancelled the operation")
            return {
                "past_steps": [("执行取消", "用户取消操作")],
                "execution_status": "cancelled",
            }
    
    # 执行工具调用
    try:
        results = await agent.execute(plan_details)
        logger.info(f"[executor] Execution completed with {len(results)} results")
        
        # 构建 past_steps
        past_steps = []
        for result in results:
            task = f"{result['tool']}: {result['purpose']}"
            result_str = str(result['result'])
            past_steps.append((task, result_str))
        
        # 判断执行状态
        has_error = any("error" in r.get("result", {}) for r in results)
        execution_status = "failed" if has_error else "success"
        
        return {
            "past_steps": past_steps,
            "execution_status": execution_status,
        }
    
    except Exception as e:
        logger.error(f"[executor] Execution failed: {e}")
        return {
            "past_steps": [("执行失败", str(e))],
            "execution_status": "failed",
        }
```

## Token 优化策略

针对多次追问重复调用 LLM 的 token 浪费问题，提供以下优化方案：

### 优化方案1：智能追问合并（推荐）

**核心思想**：一次性收集所有需要的信息，而不是逐个追问。

```python
async def planner(state: MainAgentState) -> Dict[str, Any]:
    """
    Planner 节点：制定执行计划
    优化：一次性收集所有缺失信息
    """
    logger.info("[planner] Starting planning phase")
    
    agent = get_ticket_planner_agent()
    user_id = state.get("user_id", "unknown")
    messages = list(state.get("messages", []))
    
    # 第一次调用：分析需求并识别所有缺失信息
    analysis = await agent.analyze_needs(messages, user_id)
    
    # 检查是否需要更多信息
    if analysis.missing_info:
        # 一次性构建所有问题
        questions = "\n".join([
            f"{i+1}. {info.question}" 
            for i, info in enumerate(analysis.missing_info)
        ])
        
        combined_question = f"为了更好地帮助您，我需要了解以下信息：\n\n{questions}\n\n请按顺序回答以上问题。"
        
        logger.info(f"[planner] Interrupting for {len(analysis.missing_info)} info items")
        
        # 触发中断 - 一次性等待所有回答
        user_answer = interrupt(combined_question)
        
        # 解析用户回答（可以要求用户按格式回答，或用另一个 LLM 解析）
        parsed_answers = await agent.parse_answers(user_answer, analysis.missing_info)
        
        # 将解析后的信息添加到消息中
        for answer in parsed_answers:
            messages.append(HumanMessage(content=f"{answer.field}: {answer.value}"))
        
        # 只调用一次 LLM 生成最终计划
        plan = await agent.plan(messages, user_id, skill_names=[])
        logger.info(f"[planner] Generated plan after collecting all info: {plan.target}")
    else:
        # 不需要追问，直接生成计划
        plan = await agent.plan(messages, user_id, skill_names=[])
    
    return {
        "plan": [...],
        "plan_details": plan.model_dump(),
        "messages": messages,
    }
```

**优点**：

* 无论多少轮追问，只调用 2 次 LLM（分析 + 最终规划）

* 用户体验更好，一次性回答所有问题

**缺点**：

* 需要设计良好的问题解析逻辑

* 用户可能不按格式回答

### 优化方案2：减少 LLM 调用次数（真正节省 Token）

**重要说明**：缓存在内存中**不会节省 token**，因为 LLM 每次调用都会重新处理完整的上下文。真正节省 token 的方法是**减少 LLM 调用次数**。

#### 核心思想

通过智能分析，一次性收集所有缺失信息，将多次 LLM 调用减少为 2 次（分析 + 最终规划）。

#### 节省 Token 的原理

**传统方式**（多次调用 LLM）：

```
第1次：分析需求（2000 tokens）
第2次：追问后重新规划（2200 tokens）
第3次：再次追问后规划（2300 tokens）
总计：6500 tokens，3 次 LLM 调用
```

**优化方式**（减少调用次数）：

```
第1次：分析需求，识别所有缺失信息（2000 tokens）
    ↓ 中断，一次性收集所有信息
第2次：基于完整信息生成最终计划（2100 tokens）
总计：4100 tokens，2 次 LLM 调用
```

**节省效果**：6500 → 4100，**节省约 37%**，且减少 1 次 LLM 调用

#### 代码实现

```python
async def planner(state: MainAgentState) -> Dict[str, Any]:
    """
    Planner 节点：制定执行计划
    优化：减少 LLM 调用次数，真正节省 token
    """
    logger.info("[planner] Starting planning phase")
    
    agent = get_ticket_planner_agent()
    user_id = state.get("user_id", "unknown")
    messages = list(state.get("messages", []))
    
    # 第1次 LLM 调用：分析需求并识别所有缺失信息
    # 使用轻量级 prompt，只分析不生成完整计划
    analysis = await agent.analyze_needs(messages, user_id)
    
    # 检查是否需要更多信息
    if analysis.missing_info:
        # 一次性构建所有问题
        questions = "\n".join([
            f"{i+1}. {info.question}" 
            for i, info in enumerate(analysis.missing_info)
        ])
        
        combined_question = f"为了更好地帮助您，我需要了解以下信息：\n\n{questions}\n\n请按顺序回答以上问题。"
        
        logger.info(f"[planner] Interrupting for {len(analysis.missing_info)} info items")
        
        # 触发中断 - 一次性等待所有回答
        user_answer = interrupt(combined_question)
        
        # 解析用户回答（可以用规则或轻量级 LLM）
        parsed_answers = parse_answers_simple(user_answer, analysis.missing_info)
        
        # 将解析后的信息添加到消息中
        for field, value in parsed_answers.items():
            messages.append(HumanMessage(content=f"{field}: {value}"))
        
        # 第2次 LLM 调用：基于完整信息生成最终计划
        plan = await agent.plan(messages, user_id, skill_names=[])
        logger.info(f"[planner] Generated plan after collecting all info: {plan.target}")
    else:
        # 不需要追问，直接生成计划（总共只调用 1 次 LLM）
        plan = await agent.plan(messages, user_id, skill_names=[])
    
    return {
        "plan": [...],
        "plan_details": plan.model_dump(),
        "messages": messages,
    }


def parse_answers_simple(user_answer: str, missing_info: List[MissingInfo]) -> Dict[str, str]:
    """
    简单解析用户回答
    使用规则匹配，不需要调用 LLM
    """
    parsed = {}
    lines = user_answer.strip().split('\n')
    
    for i, info in enumerate(missing_info):
        # 尝试按编号匹配
        for line in lines:
            # 匹配 "1. 答案" 或 "1、答案" 或 "答案" 格式
            if line.strip().startswith(f"{i+1}.") or line.strip().startswith(f"{i+1}、"):
                parsed[info.field] = line.split('.', 1)[-1].split('、', 1)[-1].strip()
                break
            # 如果只有一行，直接作为第一个问题的答案
            elif len(lines) == 1 and i == 0:
                parsed[info.field] = user_answer.strip()
                break
    
    return parsed
```

#### 关键优化点

1. **分离分析和规划**：

   * 第1次调用：轻量级分析，只识别缺失信息

   * 第2次调用：基于完整信息生成最终计划

2. **一次性收集信息**：

   * 不逐个追问，一次性列出所有问题

   * 用户一次性回答，减少交互轮次

3. **轻量级解析**：

   * 使用规则解析用户回答，不调用 LLM

   * 只有生成最终计划时才调用 LLM

#### 适用场景

* **多轮追问场景**：问题越多，节省效果越明显

* **信息收集类任务**：需要用户提供多个字段

* **对响应速度有要求**：减少 LLM 调用次数，提升响应速度

#### 优缺点总结

**优点**：

* **真正节省 token**：减少 LLM 调用次数，从根本上节省 token

* **提升响应速度**：减少 LLM 调用，降低网络延迟

* **用户体验好**：一次性回答所有问题，减少等待

* **实现简单**：不需要复杂的缓存机制

**缺点**：

* **需要设计解析逻辑**：用户可能不按格式回答

* **不适合复杂场景**：如果追问逻辑很复杂，可能需要多次调用 LLM

### 优化方案3：轻量级追问（简单场景）

**核心思想**：追问时不重新调用 LLM，直接基于规则生成计划。

```python
async def planner(state: MainAgentState) -> Dict[str, Any]:
    """
    Planner 节点：制定执行计划
    优化：简单追问不调用 LLM
    """
    agent = get_ticket_planner_agent()
    user_id = state.get("user_id", "unknown")
    messages = list(state.get("messages", []))
    
    # 第一次调用 LLM 生成计划
    plan = await agent.plan(messages, user_id, skill_names=[])
    
    # 检查是否需要更多信息
    if plan.need_more_info:
        question = plan.clarify_question
        user_answer = interrupt(question)
        
        # 简单场景：直接填充缺失信息，不重新调用 LLM
        if plan.missing_field and plan.is_simple_fill:
            # 基于规则填充
            plan = agent.fill_missing_info(plan, plan.missing_field, user_answer)
            logger.info(f"[planner] Filled missing info without LLM call")
        else:
            # 复杂场景：重新调用 LLM
            messages.append(HumanMessage(content=user_answer))
            plan = await agent.plan(messages, user_id, skill_names=[])
    
    return {"plan": plan, ...}
```

**优点**：

* 简单场景零额外 token 消耗

* 实现简单

**缺点**：

* 只适用于简单填充场景

* 复杂场景仍需调用 LLM

### 优化方案4：主模型 + 轻量模型 + 状态缓存（最终推荐）

**核心思想**：
1. **第一次**：用主模型（GPT-4）一次性分析并收集所有缺失信息
2. **后续追问**：用轻量模型（本地小模型）验收用户回答和追问
3. **重新规划**：用状态缓存（已收集的信息）重新调用主模型生成计划

#### 工作流程

```
用户输入
    ↓
[主模型] 分析需求，识别所有缺失信息
    ↓
一次性列出所有问题（中断）
    ↓
用户回答
    ↓
[轻量模型] 验收回答是否完整
    ├─ 完整 → 用缓存信息调用主模型生成计划
    └─ 不完整 → 轻量模型追问（循环，最多3次）
                ↓
          用户补充回答
                ↓
          [主模型] 基于完整缓存生成最终计划
```

#### 代码实现

```python
async def planner(state: MainAgentState) -> Dict[str, Any]:
    """
    Planner 节点：制定执行计划
    策略：主模型收集 → 轻量模型验收/追问 → 主模型规划
    """
    logger.info("[planner] Starting planning phase")
    
    agent = get_ticket_planner_agent()
    user_id = state.get("user_id", "unknown")
    messages = list(state.get("messages", []))
    
    # 获取当前状态
    clarification_count = state.get("clarification_count", 0)
    max_clarifications = 3
    collected_info = state.get("collected_info", {})  # 已收集的信息缓存
    
    if clarification_count == 0:
        # ========== 第1阶段：主模型一次性分析 ==========
        logger.info("[planner] Phase 1: Main model analysis")
        
        # 使用主模型分析需求，识别所有缺失信息
        analysis = await agent.analyze_needs(messages, user_id)
        
        if analysis.missing_info:
            # 构建一次性问题列表
            questions = "\n".join([
                f"{i+1}. {info.question}" 
                for i, info in enumerate(analysis.missing_info)
            ])
            combined_question = f"为了更好地帮助您，我需要了解以下信息：\n\n{questions}\n\n请按顺序回答。"
            
            # 保存需要收集的字段到状态（缓存）
            return {
                "plan": None,
                "clarification_count": 1,
                "pending_questions": [info.field for info in analysis.missing_info],
                "collected_info": {},
                "need_interrupt": True,
                "interrupt_question": combined_question
            }
        else:
            # 不需要追问，直接生成计划
            plan = await agent.plan(messages, user_id, skill_names=[])
            return {"plan": plan, "collected_info": {}}
    
    else:
        # ========== 第2阶段：轻量模型验收/追问 ==========
        logger.info(f"[planner] Phase 2: Light model validation (round {clarification_count})")
        
        # 获取用户最新回答
        user_answer = state.get("last_user_answer", "")
        pending_questions = state.get("pending_questions", [])
        
        # 使用轻量模型（本地小模型）验收回答
        light_agent = get_lightweight_agent()  # 轻量模型代理
        validation = await light_agent.validate_answer(
            user_answer=user_answer,
            pending_questions=pending_questions,
            collected_info=collected_info
        )
        
        # 更新已收集的信息（状态缓存）
        collected_info.update(validation.extracted_info)
        
        if validation.is_complete:
            # ========== 第3阶段：主模型生成最终计划 ==========
            logger.info("[planner] Phase 3: Main model planning with cached info")
            
            # 将缓存的信息添加到消息中
            for field, value in collected_info.items():
                messages.append(HumanMessage(content=f"{field}: {value}"))
            
            # 使用主模型生成最终计划（只调用1次）
            plan = await agent.plan(messages, user_id, skill_names=[])
            
            return {
                "plan": plan,
                "collected_info": collected_info,
                "clarification_count": 0,
                "pending_questions": [],
                "messages": messages
            }
        
        elif clarification_count < max_clarifications:
            # 信息不完整，轻量模型继续追问
            follow_up_question = validation.follow_up_question or "请补充以下信息："
            
            return {
                "plan": None,
                "clarification_count": clarification_count + 1,
                "pending_questions": validation.remaining_questions,
                "collected_info": collected_info,  # 缓存已收集的信息
                "need_interrupt": True,
                "interrupt_question": follow_up_question
            }
        
        else:
            # 超过最大追问次数，用已有信息生成计划
            logger.warning("[planner] Max clarification reached, generating plan with partial info")
            
            for field, value in collected_info.items():
                messages.append(HumanMessage(content=f"{field}: {value}"))
            
            plan = await agent.plan(messages, user_id, skill_names=[])
            
            return {
                "plan": plan,
                "collected_info": collected_info,
                "clarification_count": 0,
                "messages": messages
            }


class LightweightValidator:
    """
    轻量模型验证器
    使用本地小模型（如 Ollama 的 llama3.1:8b）验收用户回答
    """
    
    def __init__(self):
        self._llm = get_local_llm()  # 使用本地轻量模型
    
    async def validate_answer(
        self,
        user_answer: str,
        pending_questions: List[str],
        collected_info: Dict[str, str]
    ) -> ValidationResult:
        """
        验证用户回答是否完整
        
        Returns:
            ValidationResult:
                - is_complete: 是否完整
                - extracted_info: 提取的信息
                - remaining_questions: 剩余问题
                - follow_up_question: 追问问题
        """
        # 使用轻量模型分析用户回答
        prompt = f"""
        请分析用户的回答，提取所需信息。
        
        待收集的字段：{', '.join(pending_questions)}
        用户回答：{user_answer}
        已收集的信息：{collected_info}
        
        请输出 JSON 格式：
        {{
            "is_complete": true/false,
            "extracted_info": {{"字段名": "提取的值"}},
            "remaining_questions": ["剩余字段"],
            "follow_up_question": "追问问题（如果不完整）"
        }}
        """
        
        response = await self._llm.ainvoke(prompt)
        # 解析响应...
        return ValidationResult(**parsed_response)
```

#### 状态缓存说明

**什么是状态缓存？**
- 利用 LangGraph 的 State 机制，在节点间传递数据
- `collected_info` 字段存储已收集的用户信息
- 每次中断恢复后，状态会自动恢复，信息不会丢失

**缓存的生命周期**：
- 创建：第一次主模型分析时创建
- 更新：每次轻量模型验收后更新
- 使用：最终调用主模型生成计划时使用
- 销毁：workflow 结束后自动清理

#### Token 消耗对比

**传统方式**（多次调用主模型）：
```
第1次（主模型）：分析 + 追问 = 2000 tokens
第2次（主模型）：重新规划 = 2200 tokens
第3次（主模型）：再次规划 = 2300 tokens
总计：6500 tokens（全部使用昂贵的 GPT-4）
```

**优化方式**（主模型 + 轻量模型）：
```
第1次（主模型）：分析 = 1500 tokens
第2次（轻量模型）：验收 = 100 tokens（本地模型，几乎免费）
第3次（轻量模型）：追问 = 100 tokens（本地模型，几乎免费）
第4次（主模型）：最终规划 = 1800 tokens
总计：3500 tokens（其中 300 tokens 是免费的本地模型）
```

**节省效果**：6500 → 3500，**节省约 46%**，且大部分使用免费本地模型

#### 优势总结

1. **显著节省 Token**：主模型只调用 2 次（分析 + 规划），追问使用轻量模型
2. **提升响应速度**：轻量模型本地运行，响应快（100-500ms）
3. **用户体验好**：一次性列出所有问题，减少交互轮次
4. **成本极低**：轻量模型本地部署，无 API 费用
5. **信息不丢失**：利用 State 机制缓存已收集的信息

## 推荐实施策略

**最终推荐方案**：

1. **首次分析**：使用主模型（GPT-4）一次性识别所有缺失信息
2. **验收/追问**：使用轻量模型（本地 Ollama）验收用户回答和追问
3. **状态缓存**：利用 LangGraph State 缓存已收集的信息
4. **最终规划**：使用主模型基于完整缓存生成计划
5. **兜底策略**：设置最大追问次数（3 次），防止无限循环

**预期效果**：

* Token 消耗减少 40%-50%
* API 调用成本降低 60%-70%（大部分使用本地模型）
* 响应速度提升（轻量模型本地运行）
* 用户体验提升（一次性列出所有问题）

## 总结

**推荐采用方案A（在 Agent 节点内部实现中断）**，并结合上述 token 优化策略：

1. 符合 LangGraph 的设计哲学
2. 业务逻辑内聚，代码直观
3. 通过智能追问合并和缓存机制，大幅减少 token 消耗
4. 异常处理方便
5. 与官方示例一致

实施时需要注意：

* 确保 interrupt 的 value 是可序列化的

* 正确处理恢复后的状态更新

* 在日志中记录中断和恢复的关键信息

* 设置最大追问次数防止无限循环

* 设计良好的问题解析逻辑，确保用户回答能被正确解析

