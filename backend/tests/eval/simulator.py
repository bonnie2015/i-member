"""用户模拟 Agent（Ticket / Recommend E2E 专用）。

LLM Agent，system prompt = 场景描述，扮演该用户，自主完成多轮对话。
使用 structured output（JSON）确保可靠地检测结束信号。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class SimulatorOutput(BaseModel):
    message: str = ""
    should_end: bool = False


class UserSimulator:
    """LLM 驱动的用户模拟器。

    使用 DeepSeek (judge role)，structured JSON 输出。
    """

    def __init__(self, scenario: str, user_id: str = "test_user_001"):
        self.scenario = scenario
        self.user_id = user_id
        self._llm = None
        self._system_prompt = self._build_system_prompt()
        self._conversation: list[dict] = []

    def _get_llm(self):
        if self._llm is None:
            from app.llm.llm_factory import get_llm

            self._llm = get_llm("judge").with_structured_output(SimulatorOutput)
        return self._llm

    def _build_system_prompt(self) -> str:
        template_path = Path(__file__).parent / "prompts" / "user_simulator.txt"
        template = template_path.read_text(encoding="utf-8")
        return template.format(scenario=self.scenario, user_id=self.user_id)

    async def start_conversation(self) -> str:
        """生成对话的首条用户消息（基于场景主动发起）。"""
        llm = self._get_llm()
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": "现在开始对话。请根据你的人设发出第一条消息，向客服表达你的需求。should_end 设为 false（对话刚开始）。",
            },
        ]
        response: SimulatorOutput = await llm.ainvoke(messages)
        text = response.message.strip() or "你好"
        self._conversation.append({"role": "user", "content": text})
        return text

    async def next_message(self, system_reply: str) -> str | None:
        """根据系统的最新回复，决定下一句用户消息。

        Returns:
            下一条用户消息，或 None 表示对话结束。
        """
        self._conversation.append({"role": "assistant", "content": system_reply})

        llm = self._get_llm()
        messages = [{"role": "system", "content": self._system_prompt}]
        messages.extend(self._conversation)

        response: SimulatorOutput = await llm.ainvoke(messages)
        text = response.message.strip()
        if response.should_end:
            if not text:
                return None
            self._conversation.append({"role": "user", "content": text})
            return None  # 最后一句话记入 transcript 但返回 None 结束循环

        if text:
            self._conversation.append({"role": "user", "content": text})
        return text or None

    @property
    def transcript(self) -> list[dict]:
        """返回对话记录，供 ModelGrader 使用。"""
        return list(self._conversation)
