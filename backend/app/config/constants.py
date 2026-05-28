"""i-member 全局配置常量。避免各模块散落硬编码的 magic number。"""

# ---- ticket executor ----
TICKET_EXECUTOR_MAX_TOOL_CALLS = 5
TRY_PROCESS_MAX_TOKENS = 2500

# ---- ticket planner / replan ----
MAX_REPLAN = 2

# ---- qa ----
QA_MAX_TOOL_CALLS = 3
MAX_QA_TOKENS = 2000

# ---- recommend ----
RECOMMEND_MAX_TOOL_BLOCKS = 2
RECOMMEND_MAX_TOOL_CALLS = 3
RECOMMEND_MAX_ROUNDS = 20

# ---- memory ----
SERVICE_MEMORY_KEEP_COUNT = 10
SERVICE_MEMORY_TTL_SECONDS = 2 * 24 * 60 * 60
USER_FACTS_LIMIT = 8
USER_FACTS_TTL_SECONDS = 30 * 24 * 60 * 60
