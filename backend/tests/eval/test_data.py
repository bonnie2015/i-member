"""E2E 测试数据管理。

Mock SCRM 预先在 seed JSON 中包含 test_user_001 的订单和档案。
测试前调 reset_test_state() 重置状态到 seed 数据，确保环境一致。
"""

from __future__ import annotations

import httpx

SCRM_URL = "http://scrm:3658"


async def reset_test_state() -> dict:
    """重置 Mock SCRM 状态到 seed 数据。每次 E2E 测试前调用。"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{SCRM_URL}/mock/admin/reset")
        resp.raise_for_status()
        return resp.json()


async def get_test_state() -> dict:
    """获取完整的 Mock SCRM 状态（调试用）。"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{SCRM_URL}/mock/admin/state")
        resp.raise_for_status()
        return resp.json()
