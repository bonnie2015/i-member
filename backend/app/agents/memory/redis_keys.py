"""
Redis key helpers.

约定：
- 所有用户态数据必须显式按 user_id 分区
- 会话运行态由 checkpointer 按 thread_id 负责，这里只定义业务侧 Redis key
"""


def user_profile_key(user_id: str) -> str:
    return f"uc:profile:{user_id}"


def user_behavior_key(user_id: str) -> str:
    return f"uc:behavior:{user_id}"


def long_term_memory_key(user_id: str) -> str:
    return f"mem:{user_id}"


def service_history_key(user_id: str) -> str:
    return f"svc:hist:{user_id}"


def service_summary_key(user_id: str) -> str:
    return f"svc:summary:{user_id}"


def compensation_day_key(user_id: str, day: str) -> str:
    return f"comp:day:{user_id}:{day}"


def compensation_week_key(user_id: str) -> str:
    return f"comp:week:{user_id}"
