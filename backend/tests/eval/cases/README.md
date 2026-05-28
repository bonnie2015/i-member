# 测试用例一览

共 37 个 Agent 单测 case，分布在 9 个文件。

## Ticket 工单体系

### executor（5 case）— `agent_executor.json`

| Case | 场景 | 验证点 |
|------|------|--------|
| exec_001 | 正常查订单→提取槽位→finish | 工具调用链正确，slots 填充，finish_step 正常 |
| exec_002 | 信息不足，需向用户追问 | ask_user 中断机制 |
| exec_003 | 无法完成，挂起或失败 | 异常路径处理 |
| exec_004 | 用户要求取消 | finish_step(cancelled) |
| exec_005 | 中断恢复：消费 ask_user 结果继续 | try_process 还原消息 + 继续执行 |

### plan（6 case）— `agent_plan.json`

| Case | 场景 | 验证点 |
|------|------|--------|
| plan_001 | 退货：查订单→选商品→收原因→建单 | 多步骤规划，工具集约束 |
| plan_002 | 投诉：收集问题→建单 | 多步骤规划 |
| plan_003 | 查询已有工单，不能新建 | 步骤数上限，禁止创建 |
| plan_004 | 无理要求（赔100万） | 拒绝规划，steps=0 |
| plan_005 | 重规划：订单为空，无法继续 | 困境中仍尝试找替代路径 |
| plan_006 | 重规划：上一步成功，继续规划 | 正确衔接已执行步骤 |

### guard（4 case）— `agent_guard.json`

| Case | 场景 | 验证点 |
|------|------|--------|
| guard_001 | 信息齐全，直接选服务 | 正确匹配技能 + 提取 goal |
| guard_002 | 意图明确但缺细节 | guard 选服务，executor 追问 |
| guard_003 | 用户明确结束 | end_service |
| guard_004 | 服务类型模糊 | clarify |

## QA 咨询体系

### qa（3 case）— `agent_qa.json`

| Case | 场景 | 验证点 |
|------|------|--------|
| qa_001 | 退换货政策 RAG 检索并回答 | RAG 触发 + 回答基于检索结果 |
| qa_002 | 闲聊不触发 RAG | RAG 不误触发 |
| qa_003 | 退换货时效查询 | RAG 召回正确性 |

## Recommend 推荐体系

### recommend（5 case）— `agent_recommend.json`

| Case | 场景 | 验证点 |
|------|------|--------|
| rec_001 | 特殊查询：西红柿鸡蛋色鞋 | 非标准查询的工具调用 |
| rec_002 | 查看商品其他颜色/尺码 | get_product_detail 调用 |
| rec_003 | 用户说不好看→调整搜索 | 偏好调整后的搜索策略 |
| rec_004 | 需求矛盾：便宜但限量 | 边界情况处理 |
| rec_005 | 搜索后回复商品 | 正确调用搜索工具 + 回复 |

### recommend_guard（4 case）— `agent_recommend_guard.json`

| Case | 场景 | 验证点 |
|------|------|--------|
| recg_001 | 用户满意并道谢 | 正确判断任务完成 |
| recg_002 | 用户想看更多 | 正确判断继续推荐 |
| recg_003 | 从上一轮 trace 提取锚点 | 锚点商品抽取 |
| recg_004 | 多轮上下文合并摘要 | 累计压缩正确性 |

## Router 路由

### router（3 case）— `agent_router.json`

| Case | 场景 | 验证点 |
|------|------|--------|
| router_001 | 退货意图 | 正确路由到 ticket |
| router_002 | 商品投诉 | 正确路由到 ticket |
| router_003 | 送礼推荐 | 正确路由到 recommend |

## 后处理

### user_facts（4 case）— `agent_user_facts.json`

| Case | 场景 | 验证点 |
|------|------|--------|
| pp_004 | 从对话提取用户偏好 | 新增事实正确 |
| pp_005 | 用户改变偏好 | 删除事实正确 |
| pp_006 | 不提取无关噪音 | 拒误增 |
| pp_007 | 不误删已有事实 | 拒误删 |
