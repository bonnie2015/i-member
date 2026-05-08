from app.tools.user_interaction_tools import (
    FinishStepInput,
    InterruptDisplayProductInput,
    InterruptToolInput,
    QaReplyInput,
    RecommendationReplyInput,
    ask_user_tool,
    finish_step_tool,
    reply_to_user_tool,
    reply_with_products_tool,
)
from app.tools.memory_tools import get_memory_tools

# 品牌业务工具 — 由品牌方提供实现，详见 tools/business/*.example
try:
    from app.tools.business.product_tools import get_product_search_tools, get_product_detail
except ImportError:
    get_product_search_tools = lambda: []
    get_product_detail = lambda: None

try:
    from app.tools.business.api_tools import call_business_api, get_business_tools
except ImportError:
    call_business_api = None
    get_business_tools = lambda: []

# 兼容旧引用（品牌方替换为实际实现后这些别名仍然可用）
get_onitsuka_tools = get_product_search_tools
onitsuka_get_product_detail = get_product_detail
call_scrm_api = call_business_api
get_scrm_tools = get_business_tools
