```mermaid
graph TD
    %% =========================
    %% ① 决策层：小模型直接输出意图+情绪评分，并路由
    %% =========================
    subgraph Decision [决策层]
        Start([用户输入]) --> Router[小模型：输出 intent + emotion_score<br/>意图: service / qa / recommendation]
    end

    %% 根据意图直接路由到执行块
    Router -->|intent=service| ServiceBlock
    Router -->|intent=qa| QaBlock
    Router -->|intent=recommendation| RecBlock

    %% =========================
    %% ② 执行层（三大独立块）
    %% =========================

    subgraph ServiceBlock [工单流程]
        S1[提取信息] --> S2{信息完整？}
        S2 -->|否| S3[生成追问回复] --> S_End
        S2 -->|是| S4[匹配部门+创建工单] --> S5{重试成功？}
        S5 -->|是| S6[跟踪&通知] --> S_End
        S5 -->|否| S7[降级记录] --> S_End
        S_End(( ))
    end

    subgraph QaBlock [咨询流程]
        Q1[查询知识库/画像] --> Q2{Token预算检查}
        Q2 -->|超限| Q3[降级回复] --> Q_End
        Q2 -->|正常| Q4[个性化生成回复（草稿）] --> Q5{有购买倾向？}
        Q5 -->|是| Q6[跳转推荐流程] --> Q_End
        Q5 -->|否| Q7[正式回复] --> Q_End
        Q_End(( ))
    end

    subgraph RecBlock [推荐与购买流程]
        R1[个性化推荐] --> R2[优惠提醒]
        R2 --> R3[引导预约/试穿]
        R3 --> R4[引导下单] --> R5{自愈重试}
        R5 -->|成功| R6[生成回复] --> R_End
        R5 -->|失败| R7[记录意向] --> R_End
        R_End(( ))
    end

    %% =========================
    %% ③ 汇总层
    %% =========================
    ServiceBlock --> Merge[统一响应生成]
    QaBlock --> Merge
    RecBlock --> Merge

    %% =========================
    %% ④ 后处理层（情绪评分通过状态隐式传递）
    %% =========================
    subgraph Post [后处理层]
        direction TB
        P1[提取偏好/行为] --> P2[(写入长期记忆 Milvus)]
        P3[获取 emotion_score] --> P4{消极且高价值且未补偿？}
        P4 -->|是| P5[发送补偿券+标记]
        P4 -->|否| P6[无动作]
        P7[步骤计数器] --> P8{超阈值？} -->|是| P9[中断告警]
        P10{存在可唤醒内容？} -->|是| P11[(写入消息队列)]
    end

    Merge --> Post
    Post --> End([返回回复])

    %% 可唤醒内容检测来自咨询块
    QaBlock -.-> P10

    %% =========================
    %% ⑤ 主动触达引擎
    %% =========================
    subgraph Proactive [主动触达引擎]
        A1[行为事件] --> A3[触达决策]
        A2[定时任务] --> A3
        A3 --> A4[跨渠道推送]
    end

    P11 --> Proactive

    %% =========================
    %% ⑥ 离线系统
    %% =========================
    subgraph Offline [离线数据系统]
        O1[数据治理] --> O2[洞察分析]
    end

    End -.-> Offline

    style Decision fill:#f9f,stroke:#333
    style ServiceBlock fill:#eef,stroke:#333
    style QaBlock fill:#eef,stroke:#333
    style RecBlock fill:#eef,stroke:#333
    style Post fill:#ffe,stroke:#333
    style Proactive fill:#ccf,stroke:#333
    style Offline fill:#cfc,stroke:#333
```

```mermaid
    graph TD

    %% =========================
    %% ① 决策层（带任务判断）
    %% =========================
    subgraph Decision [决策层]
        Start([用户输入])

        Start --> TaskCheck{是否存在 active_task?}

        TaskCheck -->|是| ContinueTask[继续当前任务]
        TaskCheck -->|否| Router

        Router[小模型<br/>intent + emotion_score]

        ContinueTask --> Interrupt{是否被打断?}
        Interrupt -->|否| RouteTask
        Interrupt -->|是| Router

        Router --> RouteTask[路由到子流程]
    end

    %% =========================
    %% ② 执行层（三大流程）
    %% =========================

    subgraph ServiceBlock [工单流程]
        S1[提取信息] --> S2{信息完整？}
        S2 -->|否| S3[追问] --> S_End
        S2 -->|是| S4[创建工单] --> S5{成功？}
        S5 -->|是| S6[跟踪通知] --> S_End
        S5 -->|否| S7[降级] --> S_End
        S_End(( ))
    end

    subgraph QaBlock [咨询流程]
        Q1[查询知识库/画像] --> Q2{Token预算}
        Q2 -->|超限| Q3[降级回复] --> Q_End
        Q2 -->|正常| Q4[生成回复] --> Q5{有购买倾向？}
        Q5 -->|是| Q6[进入推荐流程]
        Q5 -->|否| Q7[直接回复]
        Q6 --> RecBlock
        Q7 --> Q_End
        Q_End(( ))
    end

    subgraph RecBlock [推荐流程]
        R1[推荐] --> R2[优惠]
        R2 --> R3[引导下单] --> R4{成功？}
        R4 -->|是| R5[回复] --> R_End
        R4 -->|否| R6[记录意向] --> R_End
        R_End(( ))
    end

    %% 路由
    RouteTask -->|service| ServiceBlock
    RouteTask -->|qa| QaBlock
    RouteTask -->|recommendation| RecBlock

    %% =========================
    %% ③ 汇总层
    %% =========================
    ServiceBlock --> Merge
    QaBlock --> Merge
    RecBlock --> Merge

    Merge[统一生成最终回复]

    %% =========================
    %% ④ 后处理层
    %% =========================
    subgraph Post [后处理]
        P1[写长期记忆]
        P2{情绪补偿判断}
        P3[补偿券]
        P4[步骤保护]
        P5[未完成任务检测]
    end

    Merge --> Post

    %% =========================
    %% ⑤ 循环（关键！）
    %% =========================
    Post --> End([返回回复])
    End --> Start

    %% =========================
    %% ⑥ 主动触达
    %% =========================
    subgraph Proactive
        A1[事件触发] --> A2[触达]
    end

    Post -->|有待跟进| Proactive

    %% =========================
    %% ⑦ 离线系统
    %% =========================
    subgraph Offline
        O1[数据治理] --> O2[洞察]
    end

    End -.-> Offline
```
