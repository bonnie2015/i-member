from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


# ---- 消息工厂 ----


@pytest.fixture
def make_human():
    def _make(content: str = ""):
        return HumanMessage(content=content)

    return _make


@pytest.fixture
def make_ai():
    def _make(content: str = "", tool_calls: list | None = None):
        if tool_calls:
            for i, tc in enumerate(tool_calls):
                if "id" not in tc:
                    tc["id"] = f"call_{i}"
        return AIMessage(content=content, tool_calls=tool_calls or [])

    return _make


@pytest.fixture
def make_tool():
    def _make(content: str = "", tool_call_id: str = "call_0", name: str = "test_tool"):
        return ToolMessage(content=content, tool_call_id=tool_call_id, name=name)

    return _make


# ---- state 骨架 ----


@pytest.fixture
def base_state():
    return {
        "thread_id": "test_thread",
        "user_id": "test_user",
        "messages": [],
    }


@pytest.fixture
def build_state(base_state):
    """构建带 overrides 的 AgentState dict。"""
    import copy

    def _build(overrides: dict | None = None) -> dict:
        state = copy.deepcopy(base_state)
        if overrides:
            _deep_merge(state, overrides)
        return state

    return _build


def _deep_merge(base: dict, overrides: dict) -> None:
    """递归合并 overrides 到 base（就地修改）。"""
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ---- JWT 测试工具 ----


@pytest.fixture
def make_test_jwt():
    """生成测试用的 HS256 JWT token。

    复用与 app/security/jwt_auth.py 相同的签名逻辑。
    """

    def _make(user_id: str = "test_user_001", expiry_minutes: int = 60) -> str:
        from app.config.config import settings

        header = {"alg": "HS256", "typ": "JWT"}
        now = int(time.time())
        payload = {
            "sub": user_id,
            "exp": now + expiry_minutes * 60,
            "iat": now,
            "iss": settings.jwt_issuer,
        }
        header_b64 = (
            base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode())
            .rstrip(b"=")
            .decode()
        )
        payload_b64 = (
            base64.urlsafe_b64encode(
                json.dumps(payload, separators=(",", ":")).encode()
            )
            .rstrip(b"=")
            .decode()
        )
        signing_input = f"{header_b64}.{payload_b64}"
        signature = (
            base64.urlsafe_b64encode(
                hmac.new(
                    settings.jwt_secret_key.encode(),
                    signing_input.encode(),
                    hashlib.sha256,
                ).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        return f"{signing_input}.{signature}"

    return _make


# ---- HTTP 客户端（E2E 用） ----


@pytest.fixture
async def eval_client(make_test_jwt):
    """async httpx client，指向 backend:8000，预置 auth header。"""
    import httpx

    token = make_test_jwt()
    async with httpx.AsyncClient(
        base_url="http://backend:8000",
        headers={"Authorization": f"Bearer {token}"},
        timeout=120.0,
    ) as client:
        yield client


# ---- Case 加载器 ----


@pytest.fixture
def load_case():
    """从 tests/eval/cases/ 加载 JSON case 文件。

    用法: case = load_case("regression/agent_router.json")  # 返回 list[dict]
    """
    from pathlib import Path

    def _load(relative_path: str) -> dict | list:
        full_path = Path(__file__).parent / "eval" / "cases" / relative_path
        return json.loads(full_path.read_text(encoding="utf-8"))

    return _load


# ---- Agent 单例 fixtures ----


@pytest.fixture(scope="session")
def router_agent():
    from app.agents.router_agent import router_agent

    return router_agent


@pytest.fixture(scope="session")
def guard_agent():
    from app.agents.ticket.guard_agent import ticket_guard_agent

    return ticket_guard_agent


@pytest.fixture(scope="session")
def plan_agent():
    from app.agents.ticket.plan_agent import ticket_plan_agent

    return ticket_plan_agent


@pytest.fixture(scope="session")
def qa_agent():
    from app.agents.qa_agent import qa_agent

    return qa_agent


@pytest.fixture(scope="session")
def recommend_agent():
    from app.agents.recommend_agent import recommend_agent

    return recommend_agent


@pytest.fixture(scope="session")
def recommend_guard_agent():
    from app.agents.recommend_guard_agent import recommend_guard_agent

    return recommend_guard_agent
