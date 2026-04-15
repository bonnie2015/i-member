# SCRM客户记忆系统 - 完整实施方案

## 一、方案概述

### 1.1 混合方案

**详细记录 + Summary**：
- **详细记录**：最近5次服务历史、5次情绪记录、3个未完成任务
- **Summary**：服务历史summary、情绪历史summary、用户画像summary、行为轨迹summary

### 1.2 Skills设计

**SCRM数据获取Skills**：
- `get_customer_profile`: 获取用户画像（含summary）
- `get_behavior_tracking`: 获取行为轨迹（含summary）

**Agent记忆管理Skills**：
- `get_service_history`: 获取服务历史（详细 + summary）
- `get_emotion_history`: 获取情绪历史（详细 + summary）
- `get_pending_tasks`: 获取未完成任务

## 二、记忆分类

### 2.1 短期记忆（会话级）

| 记忆类型 | 存储位置 | 作用域 | TTL | 管理方 |
|---------|----------|--------|-----|--------|
| **对话状态（messages）** | Redis Checkpointer | thread_id | 30天 | LangGraph |
| **current_subgraph** | Redis Checkpointer | thread_id | 30天 | Agent |

**特点**：
- 按thread_id分离
- 随对话结束而失效
- 支持多轮对话

### 2.2 长期记忆（用户级）

| 记忆类型 | 存储位置 | 作用域 | TTL | 管理方 |
|---------|----------|--------|-----|--------|
| **服务历史（详细）** | Redis | user_id | 90天 | Agent |
| **服务历史（Summary）** | Redis | user_id | 90天 | Agent |
| **情绪历史（详细）** | Redis | user_id | 90天 | Agent |
| **情绪历史（Summary）** | Redis | user_id | 90天 | Agent |
| **未完成任务** | Redis | user_id | 90天 | Agent |

**特点**：
- 按user_id分离
- 跨对话持久化
- 支持长期分析

### 2.3 SCRM数据（缓存）

| 数据类型 | 存储位置 | 作用域 | TTL | 管理方 |
|---------|----------|--------|-----|--------|
| **用户画像** | Redis缓存 | user_id | 30分钟 | SCRM系统 |
| **行为轨迹** | Redis缓存 | user_id | 5分钟 | SCRM系统 |

**特点**：
- 从SCRM系统获取
- 有缓存机制
- 不属于Agent记忆

## 三、存储结构详解

### 3.1 短期记忆存储结构

```
Redis Key: checkpoint:{thread_id}
Type: Hash
Fields:
  - messages: List[BaseMessage]  # 对话消息列表
  - state: AgentState            # 完整状态
TTL: 30天
```

**示例**：
```json
{
  "messages": [
    {"type": "human", "content": "我要退货"},
    {"type": "ai", "content": "好的，请提供订单号"}
  ],
  "state": {
    "base": {
      "user_id": "user123",
      "thread_id": "thread_abc",
      "messages": [...],
      "final_reply": null
    },
    "route": {
      "current_subgraph": "service_block",
      "intent": "service",
      "confidence": {"score": 0.8}
    }
  }
}
```

### 3.2 长期记忆存储结构

#### 服务历史（详细）

```
Redis Key: agent:service_history:{user_id}
Type: String (JSON)
TTL: 90天
```

**示例**：
```json
{
  "sessions": [
    {
      "thread_id": "thread_abc",
      "intent": "service",
      "date": "2026-04-08",
      "result": "已创建工单T001",
      "satisfaction": "满意"
    },
    {
      "thread_id": "thread_def",
      "intent": "qa",
      "date": "2026-04-07",
      "result": "已解答产品咨询",
      "satisfaction": "满意"
    }
  ]
}
```

#### 服务历史（Summary）

```
Redis Key: agent:service_summary:{user_id}
Type: String
TTL: 90天
```

**示例**：
```
近3个月：工单处理3次，咨询5次，推荐2次；平均满意度：满意
```

#### 情绪历史（详细）

```
Redis Key: agent:emotion_history:{user_id}
Type: String (JSON)
TTL: 90天
```

**示例**：
```json
{
  "recent_scores": [
    {
      "thread_id": "thread_abc",
      "date": "2026-04-08",
      "score": 0.7
    },
    {
      "thread_id": "thread_def",
      "date": "2026-04-07",
      "score": 0.8
    }
  ],
  "compensation_records": [
    {
      "thread_id": "thread_xyz",
      "date": "2026-03-15",
      "type": "补偿券",
      "amount": 50.00
    }
  ],
  "average_score": 0.75,
  "trend": "stable"
}
```

#### 情绪历史（Summary）

```
Redis Key: agent:emotion_summary:{user_id}
Type: String
TTL: 90天
```

**示例**：
```
平均情绪0.75，趋势稳定，曾发送补偿券1次(50元)
```

#### 未完成任务

```
Redis Key: agent:pending_tasks:{user_id}
Type: String (JSON Array)
TTL: 90天
```

**示例**：
```json
[
  {
    "thread_id": "thread_xyz",
    "type": "缺货提醒",
    "status": "待通知",
    "created_at": "2026-04-01"
  }
]
```

### 3.3 SCRM数据存储结构

#### 用户画像（缓存）

```
Redis Key: scrm:profile:{user_id}
Type: String (JSON)
TTL: 30分钟
```

**示例**：
```json
{
  "basic_info": {
    "member_level": "VIP",
    "lifecycle_stage": "活跃",
    "total_orders": 10,
    "total_spent": 5000.00
  },
  "preferences": {
    "favorite_categories": ["服装", "数码"],
    "price_sensitivity": "中",
    "brand_preference": ["品牌A", "品牌B"]
  },
  "behavior_patterns": {
    "active_hours": ["晚上8-10点"],
    "purchase_frequency": "每月1-2次"
  }
}
```

#### 用户画像Summary（缓存）

```
Redis Key: scrm:profile_summary:{user_id}
Type: String
TTL: 30分钟
```

**示例**：
```
VIP客户，偏好服装、数码类商品，价格敏感度中等，喜欢新品推荐
```

#### 行为轨迹（缓存）

```
Redis Key: scrm:behavior:{user_id}
Type: String (JSON)
TTL: 5分钟
```

**示例**：
```json
{
  "browsing_history": [
    {
      "product_id": "P001",
      "product_name": "春季新款T恤",
      "view_count": 3,
      "last_view": "2026-04-08T20:30:00Z"
    }
  ],
  "cart_history": [
    {
      "product_id": "P002",
      "added_at": "2026-04-07T19:00:00Z",
      "status": "未支付"
    }
  ],
  "purchase_history": [
    {
      "order_id": "O001",
      "products": ["P001"],
      "amount": 500.00,
      "purchased_at": "2026-03-01T15:00:00Z"
    }
  ]
}
```

#### 行为轨迹Summary（缓存）

```
Redis Key: scrm:behavior_summary:{user_id}
Type: String
TTL: 5分钟
```

**示例**：
```
最近浏览：春季T恤(3次)、夏季裙子(2次)；购物车：夏季裙子(未支付)；最近购买：春季T恤
```

## 四、存储结构对比

### 4.1 按作用域分类

| 作用域 | Key前缀 | 示例 | 用途 |
|--------|---------|------|------|
| **thread_id** | `checkpoint:` | `checkpoint:thread_abc` | 短期记忆（对话状态） |
| **user_id** | `agent:` | `agent:service_history:user123` | 长期记忆（Agent管理） |
| **user_id** | `scrm:` | `scrm:profile:user123` | SCRM数据（缓存） |

### 4.2 按TTL分类

| TTL | Key类型 | 示例 |
|-----|---------|------|
| **5分钟** | 行为轨迹缓存 | `scrm:behavior:user123` |
| **30分钟** | 用户画像缓存 | `scrm:profile:user123` |
| **30天** | 对话状态 | `checkpoint:thread_abc` |
| **90天** | 长期记忆 | `agent:service_history:user123` |

## 五、数据流向

```
用户消息
    ↓
【短期记忆】加载对话状态（Redis Checkpointer）
    ↓
意图识别（不注入记忆）
    ↓
业务处理
    ↓
【SCRM数据】获取用户画像/行为轨迹（缓存）
【长期记忆】获取服务历史/情绪历史（Redis）
    ↓
注入到prompt
    ↓
生成回复
    ↓
【短期记忆】保存对话状态
【长期记忆】更新服务历史/情绪历史
【SCRM数据】清除缓存
```

## 六、Skills设计

### 6.1 SCRM数据获取Skills

```python
# backend/app/skills/scrm_tools.py

from langchain_core.tools import tool
from typing import Dict, Any, Tuple
from app.services.scrm_api import scrm_api
from app.core.config import settings
from app.core.logging import get_logger
import json

logger = get_logger("scrm_tools")


@tool
async def get_customer_profile(user_id: str) -> Dict[str, Any]:
    """
    获取客户画像信息
    
    Args:
        user_id: 用户ID
    
    Returns:
        客户画像数据，包含：
        - basic_info: 基本信息（会员等级、生命周期等）
        - preferences: 偏好（品类、价格敏感度等）
        - behavior_patterns: 行为模式
        - summary: 画像摘要
    """
    try:
        # 尝试从缓存获取
        cache_key = f"scrm:profile:{user_id}"
        summary_key = f"scrm:profile_summary:{user_id}"
        
        cached_data = await redis.get(cache_key)
        cached_summary = await redis.get(summary_key)
        
        if cached_data and cached_summary:
            logger.info(f"从缓存获取用户画像: {user_id}")
            profile = json.loads(cached_data)
            return {
                **profile,
                "summary": cached_summary
            }
        
        # 从SCRM系统获取
        logger.info(f"从SCRM系统获取用户画像: {user_id}")
        profile = await scrm_api.get_customer_profile(user_id)
        
        # 生成summary
        summary = await _generate_profile_summary(profile)
        
        # 缓存（30分钟）
        await redis.setex(cache_key, 1800, json.dumps(profile, ensure_ascii=False))
        await redis.setex(summary_key, 1800, summary)
        
        return {
            **profile,
            "summary": summary
        }
        
    except Exception as e:
        logger.error(f"获取用户画像失败: {e}")
        return {
            "basic_info": {},
            "preferences": {},
            "behavior_patterns": {},
            "summary": "获取用户画像失败"
        }


@tool
async def get_behavior_tracking(user_id: str) -> Dict[str, Any]:
    """
    获取客户行为轨迹
    
    Args:
        user_id: 用户ID
    
    Returns:
        行为轨迹数据，包含：
        - browsing_history: 浏览历史
        - cart_history: 购物车记录
        - purchase_history: 购买历史
        - summary: 行为摘要
    """
    try:
        # 尝试从缓存获取
        cache_key = f"scrm:behavior:{user_id}"
        summary_key = f"scrm:behavior_summary:{user_id}"
        
        cached_data = await redis.get(cache_key)
        cached_summary = await redis.get(summary_key)
        
        if cached_data and cached_summary:
            logger.info(f"从缓存获取行为轨迹: {user_id}")
            behavior = json.loads(cached_data)
            return {
                **behavior,
                "summary": cached_summary
            }
        
        # 从SCRM系统获取
        logger.info(f"从SCRM系统获取行为轨迹: {user_id}")
        behavior = await scrm_api.get_behavior_tracking(user_id)
        
        # 生成summary
        summary = await _generate_behavior_summary(behavior)
        
        # 缓存（5分钟）
        await redis.setex(cache_key, 300, json.dumps(behavior, ensure_ascii=False))
        await redis.setex(summary_key, 300, summary)
        
        return {
            **behavior,
            "summary": summary
        }
        
    except Exception as e:
        logger.error(f"获取行为轨迹失败: {e}")
        return {
            "browsing_history": [],
            "cart_history": [],
            "purchase_history": [],
            "summary": "获取行为轨迹失败"
        }


# ========== Summary生成函数 ==========

async def _generate_profile_summary(profile: Dict[str, Any]) -> str:
    """生成用户画像summary"""
    parts = []
    
    basic_info = profile.get("basic_info", {})
    if basic_info.get("member_level"):
        parts.append(f"{basic_info['member_level']}客户")
    
    preferences = profile.get("preferences", {})
    if preferences.get("favorite_categories"):
        categories = "、".join(preferences['favorite_categories'][:2])
        parts.append(f"偏好{categories}类商品")
    
    if preferences.get("price_sensitivity"):
        parts.append(f"价格敏感度{preferences['price_sensitivity']}")
    
    behavior_patterns = profile.get("behavior_patterns", {})
    if behavior_patterns.get("browsing_habits"):
        parts.append(f"喜欢{behavior_patterns['browsing_habits']}")
    
    return "，".join(parts) if parts else "普通客户"


async def _generate_behavior_summary(behavior: Dict[str, Any]) -> str:
    """生成行为轨迹summary"""
    parts = []
    
    # 最近浏览（最多2个）
    browsing = behavior.get("browsing_history", [])[:2]
    if browsing:
        browsing_str = "、".join([
            f"{b['product_name']}({b['view_count']}次)" 
            for b in browsing
        ])
        parts.append(f"最近浏览：{browsing_str}")
    
    # 购物车（未支付）
    cart = behavior.get("cart_history", [])
    unpaid_cart = [c for c in cart if c['status'] == '未支付']
    if unpaid_cart:
        cart_str = "、".join([c['product_name'] for c in unpaid_cart[:2]])
        parts.append(f"购物车：{cart_str}(未支付)")
    
    # 最近购买（最多1个）
    purchase = behavior.get("purchase_history", [])[:1]
    if purchase:
        products = purchase[0].get("products", [])
        if products:
            parts.append(f"最近购买：{products[0]}")
    
    return "；".join(parts) if parts else "暂无行为记录"
```

### 6.2 Agent记忆管理Skills

```python
# backend/app/skills/memory_tools.py

from langchain_core.tools import tool
from typing import Dict, Any, List
from app.memory.agent_memory import AgentMemoryStorage
from app.core.logging import get_logger

logger = get_logger("memory_tools")


@tool
async def get_service_history(user_id: str) -> Dict[str, Any]:
    """
    获取服务历史记录
    
    Args:
        user_id: 用户ID
    
    Returns:
        服务历史数据，包含：
        - sessions: 最近5次服务会话详情
        - summary: 服务历史摘要
    """
    try:
        memory_storage = AgentMemoryStorage()
        history = await memory_storage.get_service_history(user_id)
        summary = await memory_storage.get_service_summary(user_id)
        
        logger.info(f"获取服务历史: {user_id}, 会话数: {len(history['sessions'])}")
        
        return {
            "sessions": history["sessions"],
            "summary": summary
        }
        
    except Exception as e:
        logger.error(f"获取服务历史失败: {e}")
        return {
            "sessions": [],
            "summary": "暂无服务记录"
        }


@tool
async def get_emotion_history(user_id: str) -> Dict[str, Any]:
    """
    获取情绪历史记录
    
    Args:
        user_id: 用户ID
    
    Returns:
        情绪历史数据，包含：
        - recent_scores: 最近5次情绪得分
        - compensation_records: 补偿记录
        - average_score: 平均情绪
        - trend: 情绪趋势
        - summary: 情绪历史摘要
    """
    try:
        memory_storage = AgentMemoryStorage()
        history = await memory_storage.get_emotion_history(user_id)
        summary = await memory_storage.get_emotion_summary(user_id)
        
        logger.info(f"获取情绪历史: {user_id}, 平均情绪: {history['average_score']}")
        
        return {
            **history,
            "summary": summary
        }
        
    except Exception as e:
        logger.error(f"获取情绪历史失败: {e}")
        return {
            "recent_scores": [],
            "compensation_records": [],
            "average_score": 0.5,
            "trend": "stable",
            "summary": "暂无情绪记录"
        }


@tool
async def get_pending_tasks(user_id: str) -> List[Dict[str, Any]]:
    """
    获取未完成任务
    
    Args:
        user_id: 用户ID
    
    Returns:
        未完成任务列表（最多3个）
    """
    try:
        memory_storage = AgentMemoryStorage()
        tasks = await memory_storage.get_pending_tasks(user_id)
        
        logger.info(f"获取未完成任务: {user_id}, 任务数: {len(tasks)}")
        
        return tasks
        
    except Exception as e:
        logger.error(f"获取未完成任务失败: {e}")
        return []
```

## 七、AgentMemoryStorage实现

```python
# backend/app/memory/agent_memory.py

from redis import Redis
from typing import Dict, Any, List
from app.core.config import settings
from app.core.logging import get_logger
import json
from datetime import datetime

logger = get_logger("agent_memory")


class AgentMemoryStorage:
    """Agent记忆存储（混合方案）"""
    
    def __init__(self):
        self.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self.prefix = "agent"
    
    # ========== 服务历史 ==========
    
    async def get_service_history(self, user_id: str) -> Dict[str, Any]:
        """获取服务历史"""
        key = f"{self.prefix}:service_history:{user_id}"
        data = await self.redis.get(key)
        
        if not data:
            return {"sessions": []}
        
        return json.loads(data)
    
    async def add_service_session(
        self, 
        user_id: str, 
        thread_id: str,
        intent: str,
        result: str,
        satisfaction: str
    ):
        """添加服务会话"""
        key = f"{self.prefix}:service_history:{user_id}"
        
        # 获取现有会话
        history = await self.get_service_history(user_id)
        sessions = history["sessions"]
        
        # 添加新会话
        sessions.append({
            "thread_id": thread_id,
            "intent": intent,
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "result": result,
            "satisfaction": satisfaction
        })
        
        # 只保留最近5条
        sessions = sessions[-5:]
        
        # 保存
        await self.redis.setex(
            key, 
            7776000,  # 90天
            json.dumps({"sessions": sessions}, ensure_ascii=False)
        )
        
        logger.info(f"添加服务会话: {user_id}, intent: {intent}")
    
    async def get_service_summary(self, user_id: str) -> str:
        """获取服务历史summary"""
        key = f"{self.prefix}:service_summary:{user_id}"
        summary = await self.redis.get(key)
        return summary or "暂无服务记录"
    
    async def save_service_summary(self, user_id: str, summary: str):
        """保存服务历史summary"""
        key = f"{self.prefix}:service_summary:{user_id}"
        await self.redis.setex(key, 7776000, summary)
        
        logger.info(f"保存服务历史summary: {user_id}")
    
    # ========== 情绪历史 ==========
    
    async def get_emotion_history(self, user_id: str) -> Dict[str, Any]:
        """获取情绪历史"""
        key = f"{self.prefix}:emotion_history:{user_id}"
        data = await self.redis.get(key)
        
        if not data:
            return {
                "recent_scores": [],
                "compensation_records": [],
                "average_score": 0.5,
                "trend": "stable"
            }
        
        return json.loads(data)
    
    async def add_emotion_score(
        self, 
        user_id: str, 
        thread_id: str,
        score: float
    ):
        """添加情绪得分"""
        key = f"{self.prefix}:emotion_history:{user_id}"
        
        # 获取现有数据
        history = await self.get_emotion_history(user_id)
        recent_scores = history["recent_scores"]
        
        # 添加新记录
        recent_scores.append({
            "thread_id": thread_id,
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "score": score
        })
        
        # 只保留最近5条
        recent_scores = recent_scores[-5:]
        
        # 计算平均情绪
        average_score = sum(s["score"] for s in recent_scores) / len(recent_scores)
        
        # 计算情绪趋势
        trend = self._calculate_trend(recent_scores)
        
        # 保存
        history["recent_scores"] = recent_scores
        history["average_score"] = round(average_score, 2)
        history["trend"] = trend
        
        await self.redis.setex(key, 7776000, json.dumps(history, ensure_ascii=False))
        
        logger.info(f"添加情绪得分: {user_id}, score: {score}")
    
    async def add_compensation_record(
        self,
        user_id: str,
        thread_id: str,
        compensation_type: str,
        amount: float
    ):
        """添加补偿记录"""
        key = f"{self.prefix}:emotion_history:{user_id}"
        
        # 获取现有数据
        history = await self.get_emotion_history(user_id)
        compensation_records = history.get("compensation_records", [])
        
        # 添加新记录
        compensation_records.append({
            "thread_id": thread_id,
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "type": compensation_type,
            "amount": amount
        })
        
        # 只保留最近3条
        compensation_records = compensation_records[-3:]
        
        # 保存
        history["compensation_records"] = compensation_records
        await self.redis.setex(key, 7776000, json.dumps(history, ensure_ascii=False))
        
        logger.info(f"添加补偿记录: {user_id}, type: {compensation_type}, amount: {amount}")
    
    async def get_emotion_summary(self, user_id: str) -> str:
        """获取情绪历史summary"""
        key = f"{self.prefix}:emotion_summary:{user_id}"
        summary = await self.redis.get(key)
        return summary or "暂无情绪记录"
    
    async def save_emotion_summary(self, user_id: str, summary: str):
        """保存情绪历史summary"""
        key = f"{self.prefix}:emotion_summary:{user_id}"
        await self.redis.setex(key, 7776000, summary)
        
        logger.info(f"保存情绪历史summary: {user_id}")
    
    def _calculate_trend(self, scores: List[Dict]) -> str:
        """计算情绪趋势"""
        if len(scores) < 3:
            return "stable"
        
        recent = [s["score"] for s in scores[-3:]]
        if recent[0] < recent[-1]:
            return "improving"
        elif recent[0] > recent[-1]:
            return "declining"
        else:
            return "stable"
    
    # ========== 未完成任务 ==========
    
    async def get_pending_tasks(self, user_id: str) -> List[Dict[str, Any]]:
        """获取未完成任务"""
        key = f"{self.prefix}:pending_tasks:{user_id}"
        data = await self.redis.get(key)
        
        if not data:
            return []
        
        return json.loads(data)
    
    async def add_pending_task(self, user_id: str, task: Dict[str, Any]):
        """添加未完成任务"""
        key = f"{self.prefix}:pending_tasks:{user_id}"
        
        # 获取现有任务
        tasks = await self.get_pending_tasks(user_id)
        
        # 添加新任务
        tasks.append(task)
        
        # 只保留最近3个
        tasks = tasks[-3:]
        
        # 保存
        await self.redis.setex(key, 7776000, json.dumps(tasks, ensure_ascii=False))
        
        logger.info(f"添加未完成任务: {user_id}, type: {task.get('type')}")
    
    async def remove_pending_task(self, user_id: str, thread_id: str):
        """移除未完成任务"""
        key = f"{self.prefix}:pending_tasks:{user_id}"
        
        # 获取现有任务
        tasks = await self.get_pending_tasks(user_id)
        
        # 过滤掉指定任务
        tasks = [t for t in tasks if t["thread_id"] != thread_id]
        
        # 保存
        if tasks:
            await self.redis.setex(key, 7776000, json.dumps(tasks, ensure_ascii=False))
        else:
            await self.redis.delete(key)
        
        logger.info(f"移除未完成任务: {user_id}, thread_id: {thread_id}")
```

## 八、成本分析

### 8.1 存储成本

| 项目 | 大小 | 成本 |
|------|------|------|
| **每用户记忆** | ~1.3KB | 极低 |
| **10000用户** | ~13MB | 可忽略 |
| **Redis内存** | 按需分配 | 低 |

### 8.2 读取成本

| 操作 | 频率 | 成本 |
|------|------|------|
| **Router阶段** | 每次对话 | 0次额外读取 |
| **业务阶段** | 每次对话 | 1-2次SCRM API（可能命中缓存） |
| **更新阶段** | 任务完成时 | 2-3次Redis写入 |

### 8.3 Token成本

| 阶段 | Token消耗 | 成本 |
|------|----------|------|
| **Router** | 0 tokens（无记忆注入） | 无 |
| **Service** | ~50 tokens | 低 |
| **QA** | ~100 tokens | 中 |
| **Recommend** | ~100 tokens | 中 |

## 九、实施计划

### 阶段一：Skills实现（0.5天）

1. 创建 `backend/app/skills/scrm_tools.py`
2. 创建 `backend/app/skills/memory_tools.py`
3. 实现SCRM数据获取skills
4. 实现记忆管理skills

### 阶段二：存储实现（0.5天）

1. 创建 `backend/app/memory/agent_memory.py`
2. 实现详细记录存储
3. 实现summary存储
4. 实现CRUD操作

### 阶段三：集成与测试（0.5天）

1. 在业务模块中集成skills
2. 实现记忆更新逻辑
3. 测试验证

**总计：1.5天**

## 十、文件结构

```
backend/app/
├── skills/
│   ├── __init__.py
│   ├── scrm_tools.py          # SCRM数据获取skills
│   └── memory_tools.py        # 记忆管理skills
├── memory/
│   ├── __init__.py
│   ├── agent_memory.py        # Agent记忆存储
│   └── memory_updater.py      # 记忆更新器
├── agents/nodes/
│   ├── service.py             # 工单处理（使用skills）
│   ├── qa.py                  # 咨询处理（使用skills）
│   └── recommend.py           # 推荐处理（使用skills）
└── services/
    └── scrm_api.py            # SCRM系统API封装
```

## 十一、总结

### 11.1 记忆分类

| 类型 | 作用域 | TTL | 管理方 | 用途 |
|------|--------|-----|--------|------|
| **短期记忆** | thread_id | 30天 | LangGraph | 多轮对话 |
| **长期记忆** | user_id | 90天 | Agent | 跨对话分析 |
| **SCRM数据** | user_id | 5-30分钟 | SCRM系统 | 业务数据 |

### 11.2 存储结构

| Key前缀 | 类型 | 示例 |
|---------|------|------|
| `checkpoint:` | Hash | 对话状态 |
| `agent:` | String (JSON) | 长期记忆 |
| `scrm:` | String (JSON) | SCRM数据缓存 |

### 11.3 核心优势

1. **混合方案**：详细记录 + Summary，平衡成本和稳健性
2. **Skills封装**：提高代码复用性和可维护性
3. **智能缓存**：SCRM数据缓存，减少API调用
4. **精简存储**：每用户~1.3KB，成本可控

### 11.4 稳健性提升

- ✅ 短期详细信息（最近5次）
- ✅ 长期Summary（补充稳健性）
- ✅ SCRM数据缓存（提高性能）
- ✅ Skills封装（提高可维护性）

---

**设计时间**: 2026-04-08  
**预计完成时间**: 2026-04-10  
**负责人**: AI Assistant  
**审核人**: User
