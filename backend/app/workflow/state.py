from typing import List, Dict, Any, Optional, Annotated, Literal, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    # 会话身份
    user_id: str
    thread_id: str
    channel: Literal["wechat", "app", "web", "jd", "tmall", "douyin", "api"]

    # 对话消息（add_messages reducer 自动追加）
    messages: Annotated[List[BaseMessage], add_messages]
    final_reply: Optional[str]

    # 路由元数据（不参与流控，仅供 post_process 使用）
    intent: Optional[Literal["ticket", "qa", "recommend"]]
    reason: Optional[str]

    # 多意图队列（最多3个，post_process 消费）
    intent_queue: List[str]

    # QA 专用
    qa_turn_count: int
    service_entry_message: Optional[str]  # 首条消息，连贯性检测基准

    # 情绪与任务
    emotion_score: Optional[float]  # 0=极负面，1=极正面
    pending_task: Optional[Dict[str, Any]]  # 仅 ticket / restock 两类

    # Token 监控（各节点累加）
    token_usage_total: int


class TicketState(AgentState, total=False):
    ticket_type: Optional[Literal["return_exchange", "damage_quality", "complaint", "rights_apply"]]
    reply_type: Optional[Literal["confirm", "question", "complete"]]
    ticket_id: Optional[str]
    department: Optional[str]
    info_complete: bool
    retry_count: int
    force_end: bool  # token 熔断标记

    task_context: Optional[Dict[str, Any]]  # 当前执行任务需要的动态信息数据

    # Plan-and-Execute-Reflect 循环字段
    plan: Optional[Dict[str, Any]]            # 当前执行计划（PlanOutput 序列化）
    current_step: int                          # 当前正在执行的步骤索引
    execution_results: List[Dict[str, Any]]   # 已执行步骤结果
    clarify_count: int                         # 追问轮次计数（上限 5）
    loop_count: int                            # 整体规划-执行循环次数（防止无限循环）
    collected_info: Optional[Dict[str, Any]]  # 追问过程中收集到的信息
