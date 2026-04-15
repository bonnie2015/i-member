# Workflow Test Cases

## 目的

用于测试主图从 `router` 到 `END` 的完整流程，包括：

- 路由与连续性判断
- ticket / qa / recommend 分流
- ticket 子图追问、确认、执行、反思
- 后处理情绪分析、补偿券、历史与记忆落库
- 中断恢复、状态清理、服务衔接
- 用户隔离与越权防护

## 通用 JSON 结构

每个 case 的 JSON 示例统一使用以下字段：

- `case_id`: 用例编号
- `title`: 用例标题
- `channel`: 建议测试渠道
- `user_id`: 测试用户
- `thread_id`: 建议 thread 标识，若为空表示新会话
- `precondition`: 前置状态或前置对话
- `conversation`: 本次要发送的完整对话流
- `expected`: 核心预期

说明：

- 这些 JSON 是测试场景模板，不是当前 `/api/v1/chat` 的原始请求体。
- 单轮接口测试时，通常取 `conversation` 中最后一条 `role=user` 的消息作为本次请求。
- 多轮或中断恢复测试时，需要按 `conversation` 顺序逐轮发送，并复用同一个 `thread_id`。
- `precondition` 代表需要提前构造的 Redis 状态、mock 返回、历史服务或挂起 interrupt。

## 一、路由层

### 1. 基础路由

#### 1. `ticket`

```json
{
  "case_id": 1,
  "title": "基础路由-ticket",
  "channel": "web",
  "user_id": "tc_user_001",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "我要退货"}
  ],
  "expected": {
    "intent": "ticket",
    "secondary_intents": [],
    "reason_contains": "退货",
    "enter_subgraph": "ticket"
  }
}
```

#### 2. `qa`

```json
{
  "case_id": 2,
  "title": "基础路由-qa",
  "channel": "web",
  "user_id": "tc_user_002",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "积分怎么兑换"}
  ],
  "expected": {
    "intent": "qa",
    "enter_subgraph": "qa"
  }
}
```

#### 3. `recommend`

```json
{
  "case_id": 3,
  "title": "基础路由-recommend",
  "channel": "web",
  "user_id": "tc_user_003",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "给我推荐卫衣"}
  ],
  "expected": {
    "intent": "recommend",
    "enter_subgraph": "recommend"
  }
}
```

#### 4. 多意图

```json
{
  "case_id": 4,
  "title": "基础路由-多意图",
  "channel": "web",
  "user_id": "tc_user_004",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "我要退货，顺便推荐一件新款卫衣"}
  ],
  "expected": {
    "intent": "ticket",
    "intent_queue_contains": ["recommend"]
  }
}
```

#### 5. 空消息保护

```json
{
  "case_id": 5,
  "title": "基础路由-空消息保护",
  "channel": "web",
  "user_id": "tc_user_005",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "   "}
  ],
  "expected": {
    "intent": "qa",
    "reason_contains": "消息为空"
  }
}
```

### 2. 连续性判断

#### 6. 与上一轮 ticket 明显连续

```json
{
  "case_id": 6,
  "title": "连续性判断-明显连续",
  "channel": "web",
  "user_id": "tc_user_006",
  "thread_id": "tc_user_006_followup_01",
  "precondition": {
    "last_service": {
      "intent": "ticket",
      "service_summary": "已为用户创建退货工单，订单号123456，工单号TK-001，等待退款处理",
      "final_reply": "好的，已经为您创建了退货工单，工单号TK-001。"
    }
  },
  "conversation": [
    {"role": "user", "content": "那多久退款"}
  ],
  "expected": {
    "continuity": true,
    "guard_action": "continue_last_service"
  }
}
```

#### 7. 与上一轮 ticket 连续但只是简单收口

```json
{
  "case_id": 7,
  "title": "连续性判断-简单收口",
  "channel": "web",
  "user_id": "tc_user_007",
  "thread_id": "tc_user_007_followup_01",
  "precondition": {
    "last_service": {
      "intent": "ticket",
      "service_summary": "已完成退货工单创建",
      "final_reply": "好的，已经为您创建了退货工单。"
    }
  },
  "conversation": [
    {"role": "user", "content": "哦好的"}
  ],
  "expected": {
    "continuity": true,
    "guard_action": "direct_reply",
    "do_not_enter_subgraph": true
  }
}
```

#### 8. 与上一轮 ticket 不连续

```json
{
  "case_id": 8,
  "title": "连续性判断-不连续",
  "channel": "web",
  "user_id": "tc_user_008",
  "thread_id": "tc_user_008_followup_01",
  "precondition": {
    "last_service": {
      "intent": "ticket",
      "service_summary": "刚完成退货工单"
    }
  },
  "conversation": [
    {"role": "user", "content": "积分怎么兑换"}
  ],
  "expected": {
    "continuity": false,
    "intent": "qa"
  }
}
```

#### 9. 连续性窗口过期

```json
{
  "case_id": 9,
  "title": "连续性判断-窗口过期",
  "channel": "web",
  "user_id": "tc_user_009",
  "thread_id": "",
  "precondition": {
    "last_service": {
      "intent": "ticket",
      "service_summary": "昨日已完成退货工单",
      "expired": true
    }
  },
  "conversation": [
    {"role": "user", "content": "那什么时候退款"}
  ],
  "expected": {
    "continuity": false,
    "route_normally": true
  }
}
```

#### 10. 连续性误判保护

```json
{
  "case_id": 10,
  "title": "连续性判断-误判保护",
  "channel": "web",
  "user_id": "tc_user_010",
  "thread_id": "",
  "precondition": {
    "last_service": {
      "intent": "ticket",
      "service_summary": "已完成退货工单"
    }
  },
  "conversation": [
    {"role": "user", "content": "推荐一双鞋"}
  ],
  "expected": {
    "continuity": false,
    "intent": "recommend"
  }
}
```

## 二、Ticket 子图

### 1. 追问

#### 11. 首轮信息不足触发追问

```json
{
  "case_id": 11,
  "title": "ticket-首轮追问",
  "channel": "web",
  "user_id": "tc_user_011",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "我要退货"}
  ],
  "expected": {
    "intent": "ticket",
    "interrupt": true,
    "interrupt_type": "input",
    "question_contains": ["订单号", "退货原因"]
  }
}
```

#### 12. 一次性追问多个缺失信息

```json
{
  "case_id": 12,
  "title": "ticket-合并追问",
  "channel": "web",
  "user_id": "tc_user_012",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "我要换货"}
  ],
  "expected": {
    "interrupt": true,
    "question_style": "single_combined_question"
  }
}
```

#### 13. 用户补齐信息后继续执行

```json
{
  "case_id": 13,
  "title": "ticket-补齐信息后继续",
  "channel": "web",
  "user_id": "tc_user_013",
  "thread_id": "tc_user_013_ticket_01",
  "precondition": {
    "interrupt_pending": {
      "reply": "请提供订单号和退货原因"
    }
  },
  "conversation": [
    {"role": "user", "content": "订单123456，尺码不合适"}
  ],
  "expected": {
    "resume_interrupt": true,
    "continue_ticket_flow": true
  }
}
```

#### 14. 追问轮次达到上限

```json
{
  "case_id": 14,
  "title": "ticket-追问上限",
  "channel": "web",
  "user_id": "tc_user_014",
  "thread_id": "tc_user_014_ticket_01",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "我要退货"},
    {"role": "assistant", "content": "请提供订单号和退货原因"},
    {"role": "user", "content": "就是想退"},
    {"role": "assistant", "content": "方便说一下订单号吗"},
    {"role": "user", "content": "先帮我弄"},
    {"role": "assistant", "content": "还需要订单号"},
    {"role": "user", "content": "你查一下不行吗"}
  ],
  "expected": {
    "clarify_count_reaches_limit": true,
    "no_infinite_interrupt_loop": true
  }
}
```

#### 15. 追问中断恢复

```json
{
  "case_id": 15,
  "title": "ticket-真实interrupt恢复",
  "channel": "web",
  "user_id": "tc_user_015",
  "thread_id": "tc_user_015_ticket_01",
  "precondition": {
    "interrupt_pending": {
      "reply": "请提供订单号"
    }
  },
  "conversation": [
    {"role": "user", "content": "123456"}
  ],
  "expected": {
    "resume_interrupt": true,
    "do_not_restart_new_service": true
  }
}
```

#### 16. 非 interrupt 场景误恢复保护

```json
{
  "case_id": 16,
  "title": "ticket-误恢复保护",
  "channel": "web",
  "user_id": "tc_user_016",
  "thread_id": "tc_user_016_ticket_01",
  "precondition": {
    "last_service_completed": true
  },
  "conversation": [
    {"role": "user", "content": "我要再问一个问题"}
  ],
  "expected": {
    "resume_interrupt": false,
    "start_new_routing": true
  }
}
```

### 2. 执行与确认

#### 17. 只读步骤直接执行

```json
{
  "case_id": 17,
  "title": "ticket-只读步骤不确认",
  "channel": "web",
  "user_id": "tc_user_017",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "帮我查订单123456"}
  ],
  "expected": {
    "tool_sequence_contains": ["get_order_detail"],
    "confirm_interrupt": false
  }
}
```

#### 18. 写操作前必须确认

```json
{
  "case_id": 18,
  "title": "ticket-写操作前确认",
  "channel": "web",
  "user_id": "tc_user_018",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "订单123456要退货，原因是尺码不合适"}
  ],
  "expected": {
    "confirm_interrupt": true,
    "confirm_options": ["确认", "取消"]
  }
}
```

#### 19. 用户显式确认后执行

```json
{
  "case_id": 19,
  "title": "ticket-确认后执行",
  "channel": "web",
  "user_id": "tc_user_019",
  "thread_id": "tc_user_019_ticket_01",
  "precondition": {
    "interrupt_pending": {
      "reply": "即将为您执行：创建退货工单，确认继续吗？"
    }
  },
  "conversation": [
    {"role": "user", "content": "确认"}
  ],
  "expected": {
    "user_confirmed": true,
    "write_tool_executed": true
  }
}
```

#### 20. 用户取消

```json
{
  "case_id": 20,
  "title": "ticket-取消执行",
  "channel": "web",
  "user_id": "tc_user_020",
  "thread_id": "tc_user_020_ticket_01",
  "precondition": {
    "interrupt_pending": {
      "ui_type": "confirm"
    }
  },
  "conversation": [
    {"role": "user", "content": "取消"}
  ],
  "expected": {
    "force_end": true,
    "reply_contains": "已为您取消操作"
  }
}
```

#### 21. 普通回复不得误判为确认

```json
{
  "case_id": 21,
  "title": "ticket-普通回复不应确认",
  "channel": "web",
  "user_id": "tc_user_021",
  "thread_id": "tc_user_021_ticket_01",
  "precondition": {
    "interrupt_pending": {
      "ui_type": "confirm"
    }
  },
  "conversation": [
    {"role": "user", "content": "我知道了"}
  ],
  "expected": {
    "user_confirmed": false,
    "write_tool_executed": false
  }
}
```

#### 22. 单商品订单退货

```json
{
  "case_id": 22,
  "title": "ticket-单商品不追问哪件",
  "channel": "web",
  "user_id": "tc_user_022",
  "thread_id": "",
  "precondition": {
    "mock_order": {
      "order_id": "ORD-SINGLE-001",
      "items": [{"sku_id": "SKU-888", "name": "深蓝色卫衣", "qty": 1}]
    }
  },
  "conversation": [
    {"role": "user", "content": "订单ORD-SINGLE-001退货，坏了"}
  ],
  "expected": {
    "ask_which_item": false
  }
}
```

#### 23. 多商品订单退货

```json
{
  "case_id": 23,
  "title": "ticket-多商品需明确商品",
  "channel": "web",
  "user_id": "tc_user_023",
  "thread_id": "",
  "precondition": {
    "mock_order": {
      "order_id": "123456",
      "items": [
        {"sku_id": "SKU-001", "name": "深蓝色卫衣", "qty": 1},
        {"sku_id": "SKU-002", "name": "白色T恤", "qty": 2}
      ]
    }
  },
  "conversation": [
    {"role": "user", "content": "订单123456要退货，坏了"}
  ],
  "expected": {
    "ask_which_item": true
  }
}
```

#### 24. 订单不存在

```json
{
  "case_id": 24,
  "title": "ticket-订单不存在",
  "channel": "web",
  "user_id": "tc_user_024",
  "thread_id": "",
  "precondition": {
    "mock_order_not_found": "ORD-NOT-FOUND"
  },
  "conversation": [
    {"role": "user", "content": "订单ORD-NOT-FOUND退货"}
  ],
  "expected": {
    "write_tool_executed": false,
    "reply_contains": "未找到"
  }
}
```

#### 25. 工具失败重试

```json
{
  "case_id": 25,
  "title": "ticket-工具失败重试",
  "channel": "web",
  "user_id": "tc_user_025",
  "thread_id": "",
  "precondition": {
    "mock_tool_failure_once": "create_ticket"
  },
  "conversation": [
    {"role": "user", "content": "订单123456退货，原因是坏了"}
  ],
  "expected": {
    "reflect_action": "retry",
    "retry_count_increase": true
  }
}
```

#### 26. 重试超限

```json
{
  "case_id": 26,
  "title": "ticket-重试超限",
  "channel": "web",
  "user_id": "tc_user_026",
  "thread_id": "",
  "precondition": {
    "mock_tool_always_fail": "create_ticket"
  },
  "conversation": [
    {"role": "user", "content": "订单123456退货，原因是坏了"}
  ],
  "expected": {
    "fail_end": true,
    "no_infinite_retry": true
  }
}
```

### 3. 反思与完成

#### 27. 多步计划按顺序推进

```json
{
  "case_id": 27,
  "title": "ticket-多步推进",
  "channel": "web",
  "user_id": "tc_user_027",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "订单123456退货，商品坏了，原路退款"}
  ],
  "expected": {
    "tool_sequence_contains": ["get_order_detail", "create_ticket"],
    "step_progress_correct": true
  }
}
```

#### 28. 反思完成

```json
{
  "case_id": 28,
  "title": "ticket-反思完成",
  "channel": "web",
  "user_id": "tc_user_028",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "订单123456退货，坏了，原路退款"}
  ],
  "expected": {
    "reflect_action": "done",
    "final_reply_exists": true
  }
}
```

#### 29. 反思失败

```json
{
  "case_id": 29,
  "title": "ticket-反思失败",
  "channel": "web",
  "user_id": "tc_user_029",
  "thread_id": "",
  "precondition": {
    "mock_permanent_failure": true
  },
  "conversation": [
    {"role": "user", "content": "帮我处理退货"}
  ],
  "expected": {
    "reflect_action": "fail",
    "final_reply_exists": true
  }
}
```

## 三、QA / Recommend

#### 30. QA 正常应答

```json
{
  "case_id": 30,
  "title": "qa-正常应答",
  "channel": "web",
  "user_id": "tc_user_030",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "会员等级有几档"}
  ],
  "expected": {
    "intent": "qa",
    "final_reply_exists": true
  }
}
```

#### 31. QA 连贯轮次延续

```json
{
  "case_id": 31,
  "title": "qa-连贯延续",
  "channel": "web",
  "user_id": "tc_user_031",
  "thread_id": "tc_user_031_qa_01",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "会员等级有几档"},
    {"role": "assistant", "content": "目前有黄金、铂金等等级"},
    {"role": "user", "content": "那铂金有什么权益"}
  ],
  "expected": {
    "qa_turn_count_increase": true,
    "messages_not_reset": true
  }
}
```

#### 32. QA 失连贯重置

```json
{
  "case_id": 32,
  "title": "qa-失连贯重置",
  "channel": "web",
  "user_id": "tc_user_032",
  "thread_id": "tc_user_032_qa_01",
  "precondition": {
    "qa_turn_count": 7,
    "service_entry_message": "会员等级有几档"
  },
  "conversation": [
    {"role": "user", "content": "推荐一双鞋"}
  ],
  "expected": {
    "messages_reset_or_new_service": true
  }
}
```

#### 33. Recommend 正常路由

```json
{
  "case_id": 33,
  "title": "recommend-正常路由",
  "channel": "web",
  "user_id": "tc_user_033",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "推荐最新卫衣"}
  ],
  "expected": {
    "intent": "recommend",
    "final_reply_exists": true
  }
}
```

## 四、后处理

### 1. 情绪分析

#### 34. 明显负面

```json
{
  "case_id": 34,
  "title": "后处理-明显负面",
  "channel": "web",
  "user_id": "tc_user_034",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "太差劲了，我要退货"}
  ],
  "expected": {
    "emotion_score_lte": 0.2
  }
}
```

#### 35. 问题描述但非强负面

```json
{
  "case_id": 35,
  "title": "后处理-问题描述非强负面",
  "channel": "web",
  "user_id": "tc_user_035",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "商品坏了，想退货"}
  ],
  "expected": {
    "emotion_score_between": [0.2, 0.6]
  }
}
```

#### 36. 中性业务请求

```json
{
  "case_id": 36,
  "title": "后处理-中性业务请求",
  "channel": "web",
  "user_id": "tc_user_036",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "帮我退货"}
  ],
  "expected": {
    "emotion_score_between": [0.4, 0.7]
  }
}
```

#### 37. 正向表达

```json
{
  "case_id": 37,
  "title": "后处理-正向表达",
  "channel": "web",
  "user_id": "tc_user_037",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "谢谢，服务很好"}
  ],
  "expected": {
    "emotion_score_gte": 0.7
  }
}
```

### 2. 发券

#### 38. 低情绪且高价值用户发券

```json
{
  "case_id": 38,
  "title": "后处理-发券",
  "channel": "web",
  "user_id": "tc_user_038",
  "thread_id": "",
  "precondition": {
    "profile_tags": ["高价值"]
  },
  "conversation": [
    {"role": "user", "content": "太差劲了，我要退货"}
  ],
  "expected": {
    "coupon_issued": true,
    "final_reply_contains": "优惠券"
  }
}
```

#### 39. 非高价值用户不发券

```json
{
  "case_id": 39,
  "title": "后处理-非高价值不发券",
  "channel": "web",
  "user_id": "tc_user_039",
  "thread_id": "",
  "precondition": {
    "profile_tags": []
  },
  "conversation": [
    {"role": "user", "content": "太差劲了，我要退货"}
  ],
  "expected": {
    "coupon_issued": false
  }
}
```

#### 40. 低情绪但超每日限制

```json
{
  "case_id": 40,
  "title": "后处理-超每日限制",
  "channel": "web",
  "user_id": "tc_user_040",
  "thread_id": "",
  "precondition": {
    "daily_coupon_already_issued": true,
    "profile_tags": ["高价值"]
  },
  "conversation": [
    {"role": "user", "content": "太差劲了，我要退货"}
  ],
  "expected": {
    "coupon_issued": false
  }
}
```

#### 41. 低情绪但超周额度

```json
{
  "case_id": 41,
  "title": "后处理-超周额度",
  "channel": "web",
  "user_id": "tc_user_041",
  "thread_id": "",
  "precondition": {
    "weekly_coupon_limit_reached": true,
    "profile_tags": ["高价值"]
  },
  "conversation": [
    {"role": "user", "content": "太差劲了，我要退货"}
  ],
  "expected": {
    "coupon_issued": false
  }
}
```

#### 42. 发券接口失败

```json
{
  "case_id": 42,
  "title": "后处理-发券接口失败",
  "channel": "web",
  "user_id": "tc_user_042",
  "thread_id": "",
  "precondition": {
    "profile_tags": ["高价值"],
    "mock_coupon_api_fail": true
  },
  "conversation": [
    {"role": "user", "content": "太差劲了，我要退货"}
  ],
  "expected": {
    "main_reply_still_returned": true,
    "process_not_crash": true
  }
}
```

### 3. 后台任务

#### 43. 服务历史后台写入成功

```json
{
  "case_id": 43,
  "title": "后处理-服务历史后台任务成功",
  "channel": "web",
  "user_id": "tc_user_043",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "积分怎么兑换"}
  ],
  "expected": {
    "log_contains": "background task finished: service_history"
  }
}
```

#### 44. 长期记忆后台写入成功

```json
{
  "case_id": 44,
  "title": "后处理-长期记忆后台任务成功",
  "channel": "web",
  "user_id": "tc_user_044",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "我平时更喜欢宽松版型的深色卫衣"}
  ],
  "expected": {
    "log_contains": "background task finished: memory"
  }
}
```

#### 45. 后台任务失败

```json
{
  "case_id": 45,
  "title": "后处理-后台任务失败不影响主流程",
  "channel": "web",
  "user_id": "tc_user_045",
  "thread_id": "",
  "precondition": {
    "mock_redis_unavailable": true
  },
  "conversation": [
    {"role": "user", "content": "积分怎么兑换"}
  ],
  "expected": {
    "warning_logged": true,
    "main_reply_still_returned": true
  }
}
```

## 五、状态与记忆

#### 46. 服务结束后清理 messages

```json
{
  "case_id": 46,
  "title": "状态-服务结束清理messages",
  "channel": "web",
  "user_id": "tc_user_046",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "积分怎么兑换"}
  ],
  "expected": {
    "checkpoint_messages_cleared": true
  }
}
```

#### 47. 新服务不复用上一轮瞬态结果

```json
{
  "case_id": 47,
  "title": "状态-新服务不复用旧瞬态状态",
  "channel": "web",
  "user_id": "tc_user_047",
  "thread_id": "",
  "precondition": {
    "last_service": {
      "emotion_score": 0.0,
      "final_reply": "好的，已经为您创建了工单。"
    }
  },
  "conversation": [
    {"role": "user", "content": "积分怎么兑换"}
  ],
  "expected": {
    "old_final_reply_not_reused": true,
    "old_emotion_score_not_reused": true
  }
}
```

#### 48. 最近服务写入历史

```json
{
  "case_id": 48,
  "title": "记忆-最近服务写入历史",
  "channel": "web",
  "user_id": "tc_user_048",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "会员等级有几档"}
  ],
  "expected": {
    "service_history_increment": 1
  }
}
```

#### 49. 长期记忆仅写入客观偏好或重要事实

```json
{
  "case_id": 49,
  "title": "记忆-长期记忆只写有价值信息",
  "channel": "web",
  "user_id": "tc_user_049",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "我更喜欢黑色和宽松版型"},
    {"role": "assistant", "content": "好的，我记住了"},
    {"role": "user", "content": "谢谢"}
  ],
  "expected": {
    "memory_contains": ["黑色", "宽松版型"],
    "memory_not_contains": ["谢谢"]
  }
}
```

#### 50. 历史超上限后进入 summary

```json
{
  "case_id": 50,
  "title": "记忆-历史超上限进入summary",
  "channel": "web",
  "user_id": "tc_user_050",
  "thread_id": "",
  "precondition": {
    "service_history_count": 5
  },
  "conversation": [
    {"role": "user", "content": "再帮我查一下会员等级"}
  ],
  "expected": {
    "oldest_service_summarized": true,
    "summary_updated": true
  }
}
```

## 六、流式接口

#### 51. `chat/stream` 逐步输出

```json
{
  "case_id": 51,
  "title": "stream-逐步输出",
  "channel": "web",
  "user_id": "tc_user_051",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "我要退货"}
  ],
  "expected": {
    "response_has": ["thread_id", "reply", "interaction"]
  }
}
```

#### 52. `final_reply` 更新不重复输出

```json
{
  "case_id": 52,
  "title": "stream-final_reply增量输出",
  "channel": "web",
  "user_id": "tc_user_052",
  "thread_id": "",
  "precondition": {
    "profile_tags": ["高价值"]
  },
  "conversation": [
    {"role": "user", "content": "太差劲了，我要退货"}
  ],
  "expected": {
    "duplicate_prefix_output": false,
    "coupon_suffix_streamed_incrementally": true
  }
}
```

#### 53. interrupt 结构化返回

```json
{
  "case_id": 53,
  "title": "stream-interrupt结构化返回",
  "channel": "web",
  "user_id": "tc_user_053",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "我要退货"}
  ],
  "expected": {
    "interrupt_payload_has": ["reply", "interaction"]
  }
}
```

#### 54. 流式恢复 interrupt

```json
{
  "case_id": 54,
  "title": "stream-恢复interrupt",
  "channel": "web",
  "user_id": "tc_user_054",
  "thread_id": "tc_user_054_ticket_01",
  "precondition": {
    "interrupt_pending": true
  },
  "conversation": [
    {"role": "user", "content": "订单123456，坏了"}
  ],
  "expected": {
    "resume_interrupt": true,
    "do_not_start_new_thread": true
  }
}
```

## 七、安全与隔离

#### 55. 不同 `user_id` 的服务历史隔离

```json
{
  "case_id": 55,
  "title": "安全-服务历史隔离",
  "channel": "web",
  "user_id": "tc_user_055_a",
  "thread_id": "",
  "precondition": {
    "other_user_history_exists": {
      "user_id": "tc_user_055_b"
    }
  },
  "conversation": [
    {"role": "user", "content": "帮我看看我之前的记录"}
  ],
  "expected": {
    "other_user_history_visible": false
  }
}
```

#### 56. 不同 `user_id` 的长期记忆隔离

```json
{
  "case_id": 56,
  "title": "安全-长期记忆隔离",
  "channel": "web",
  "user_id": "tc_user_056_a",
  "thread_id": "",
  "precondition": {
    "other_user_memory_exists": {
      "user_id": "tc_user_056_b",
      "content": "喜欢红色连衣裙"
    }
  },
  "conversation": [
    {"role": "user", "content": "给我推荐卫衣"}
  ],
  "expected": {
    "other_user_memory_in_prompt": false
  }
}
```

#### 57. 订单查询越权保护

```json
{
  "case_id": 57,
  "title": "安全-订单越权保护",
  "channel": "web",
  "user_id": "tc_user_057_a",
  "thread_id": "",
  "precondition": {
    "other_user_order": {
      "user_id": "tc_user_057_b",
      "order_id": "ORD-B-001"
    }
  },
  "conversation": [
    {"role": "user", "content": "帮我查订单ORD-B-001"}
  ],
  "expected": {
    "access_denied": true
  }
}
```

#### 58. 工单查询越权保护

```json
{
  "case_id": 58,
  "title": "安全-工单越权保护",
  "channel": "web",
  "user_id": "tc_user_058_a",
  "thread_id": "",
  "precondition": {
    "other_user_ticket": {
      "user_id": "tc_user_058_b",
      "ticket_id": "TK-B-001"
    }
  },
  "conversation": [
    {"role": "user", "content": "帮我查工单TK-B-001"}
  ],
  "expected": {
    "access_denied": true
  }
}
```

#### 59. 速率限制

```json
{
  "case_id": 59,
  "title": "安全-速率限制",
  "channel": "web",
  "user_id": "tc_user_059",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "帮我查订单1"},
    {"role": "user", "content": "帮我查订单2"},
    {"role": "user", "content": "帮我查订单3"},
    {"role": "user", "content": "帮我查订单4"}
  ],
  "expected": {
    "rate_limited_when_threshold_hit": true
  }
}
```

#### 60. 工具熔断

```json
{
  "case_id": 60,
  "title": "安全-工具熔断",
  "channel": "web",
  "user_id": "tc_user_060",
  "thread_id": "",
  "precondition": {
    "scrm_continuous_failures": true
  },
  "conversation": [
    {"role": "user", "content": "帮我查订单123456"}
  ],
  "expected": {
    "breaker_triggered": true,
    "fallback_or_error_returned": true
  }
}
```

## 八、边界场景

#### 61. 只有一个字的输入

```json
{
  "case_id": 61,
  "title": "边界-单字输入",
  "channel": "web",
  "user_id": "tc_user_061",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "退"}
  ],
  "expected": {
    "process_not_crash": true
  }
}
```

#### 62. 极长输入

```json
{
  "case_id": 62,
  "title": "边界-极长输入",
  "channel": "web",
  "user_id": "tc_user_062",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "我要退货。" }
  ],
  "expected": {
    "token_protection_effective": true
  }
}
```

#### 63. 多意图且中途切换

```json
{
  "case_id": 63,
  "title": "边界-多意图中途切换",
  "channel": "web",
  "user_id": "tc_user_063",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "先退货，再告诉我会员等级"}
  ],
  "expected": {
    "main_intent": "ticket",
    "intent_queue_contains": ["qa"]
  }
}
```

#### 64. 服务结束后立即切换新意图

```json
{
  "case_id": 64,
  "title": "边界-结束后立即切新意图",
  "channel": "web",
  "user_id": "tc_user_064",
  "thread_id": "",
  "precondition": {
    "last_service": {
      "intent": "ticket",
      "service_summary": "已完成退货工单"
    }
  },
  "conversation": [
    {"role": "user", "content": "推荐一双鞋"}
  ],
  "expected": {
    "intent": "recommend"
  }
}
```

#### 65. 用户连续发送“好的”“嗯”“谢谢”

```json
{
  "case_id": 65,
  "title": "边界-连续简单回复",
  "channel": "web",
  "user_id": "tc_user_065",
  "thread_id": "tc_user_065_followup_01",
  "precondition": {
    "last_service": {
      "intent": "ticket",
      "service_summary": "已完成退货工单"
    }
  },
  "conversation": [
    {"role": "user", "content": "好的"},
    {"role": "user", "content": "嗯"},
    {"role": "user", "content": "谢谢"}
  ],
  "expected": {
    "do_not_reenter_complex_subgraph": true
  }
}
```

#### 66. 用户负面但无业务意图

```json
{
  "case_id": 66,
  "title": "边界-负面但无业务意图",
  "channel": "web",
  "user_id": "tc_user_066",
  "thread_id": "",
  "precondition": {},
  "conversation": [
    {"role": "user", "content": "太差了"}
  ],
  "expected": {
    "do_not_create_ticket": true,
    "reasonable_reply": true
  }
}
```

#### 67. 异常恢复后再次请求

```json
{
  "case_id": 67,
  "title": "边界-异常后再次请求",
  "channel": "web",
  "user_id": "tc_user_067",
  "thread_id": "tc_user_067_01",
  "precondition": {
    "last_service_failed": true
  },
  "conversation": [
    {"role": "user", "content": "重新帮我查一下会员等级"}
  ],
  "expected": {
    "start_new_service_normally": true,
    "old_failed_state_not_pollute": true
  }
}
```

## 九、执行建议

建议测试顺序：

1. 先跑基础路由与 ticket 主链路
2. 再跑 interrupt / confirm / cancel / retry
3. 再跑后处理、流式与状态清理
4. 最后跑连续性与安全隔离

建议每次记录：

- 请求体
- 返回体
- `thread_id`
- 关键日志
- Redis 状态
- 是否通过
