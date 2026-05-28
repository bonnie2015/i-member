from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import HumanMessage

from .graders import CodeGrader, GradeResult, ModelGrader


# ---- 数据结构 ----


@dataclass
class TrialResult:
    task_id: str
    trial_index: int
    passed: bool
    code_results: list[dict] = field(default_factory=list)
    model_results: dict | None = None
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    harness_output: dict = field(default_factory=dict)


@dataclass
class CaseResult:
    task_id: str
    trials: int
    passed_trials: int
    passed: bool
    threshold: int
    trial_results: list[TrialResult] = field(default_factory=list)


@dataclass
class EvalReport:
    suite: str
    total: int
    passed: int
    failed: int
    case_results: list[CaseResult] = field(default_factory=list)

    @property
    def summary(self) -> str:
        lines = [
            f"=== {self.suite} ===",
            f"Total: {self.total}, Passed: {self.passed}, Failed: {self.failed}",
        ]
        for cr in self.case_results:
            status = "PASS" if cr.passed else "FAIL"
            lines.append(f"  [{status}] {cr.task_id} ({cr.passed_trials}/{cr.trials})")
            for tr in cr.trial_results:
                if not tr.passed:
                    for r in tr.code_results:
                        if not r["passed"]:
                            lines.append(f"    code: {r['check']} -> {r['detail']}")
                    for e in tr.errors:
                        lines.append(f"    error: {e}")
        return "\n".join(lines)


# ---- EvalRunner ----


class EvalRunner:
    """加载 case JSON 并执行测试，应用 grader，输出报告。"""

    def __init__(
        self,
        *,
        case_dirs: list[str] | None = None,
        trials_override: int | None = None,
    ):
        self.code_grader = CodeGrader()
        self.model_grader = ModelGrader()
        self._case_dirs = case_dirs or ["regression"]
        self._trials_override = trials_override

    # ==================== 加载 ====================

    def load_cases(self, category: str) -> list[dict]:
        """加载 tests/eval/cases/{category}/*.json 中的所有 case。"""
        cases_dir = Path(__file__).parent / "cases" / category
        if not cases_dir.exists():
            return []

        all_cases: list[dict] = []
        for json_file in sorted(cases_dir.glob("*.json")):
            data = json.loads(json_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                all_cases.extend(data)
            else:
                all_cases.append(data)

        # 注入来源路径和 trials
        for case in all_cases:
            case["_category"] = category
            if self._trials_override is not None:
                case["trials"] = self._trials_override

        return all_cases

    # ==================== 运行 ====================

    async def run_all(self) -> EvalReport:
        """跑所有 case_dirs 下的所有 cases。"""
        all_cases: list[dict] = []
        for d in self._case_dirs:
            all_cases.extend(self.load_cases(d))

        results = []
        for case in all_cases:
            results.append(await self.run_case(case))

        passed = sum(1 for r in results if r.passed)
        return EvalReport(
            suite=", ".join(self._case_dirs),
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            case_results=results,
        )

    async def run_case(self, case: dict) -> CaseResult:
        """跑一个 case 的所有 trials。"""
        task_id = case.get("task_id", "unknown")
        trials = case.get("trials", 1)
        # threshold: regression=trials (全过), capability=ceil(trials*2/3)
        category = case.get("_category", "regression")
        threshold = trials if category == "regression" else max(1, trials * 2 // 3)

        trial_results: list[TrialResult] = []
        for i in range(trials):
            trial_result = await self.run_trial(case, i)
            trial_results.append(trial_result)

        passed_trials = sum(1 for t in trial_results if t.passed)
        return CaseResult(
            task_id=task_id,
            trials=trials,
            passed_trials=passed_trials,
            passed=passed_trials >= threshold,
            threshold=threshold,
            trial_results=trial_results,
        )

    async def run_trial(self, case: dict, trial_index: int) -> TrialResult:
        """执行一次 trial。根据 case 类型分发到对应 harness。"""
        task_id = case.get("task_id", "unknown")
        started = time.perf_counter()
        errors: list[str] = []
        harness_output: dict = {}

        try:
            if "agent" in case:
                harness_output = await self._agent_harness(case)
            elif "scenario" in case:
                harness_output = await self._e2e_user_sim_harness(case, trial_index)
            elif "turns" in case:
                harness_output = await self._e2e_fixed_harness(case, trial_index)
            else:
                errors.append("Unknown case format: no agent/scenario/turns key")
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

        # 应用 grader
        grade_result = self._apply_graders(case, harness_output, errors)

        return TrialResult(
            task_id=task_id,
            trial_index=trial_index,
            passed=grade_result.passed,
            code_results=grade_result.code_results,
            model_results=grade_result.model_results,
            errors=grade_result.errors,
            duration_ms=int((time.perf_counter() - started) * 1000),
            harness_output=harness_output,
        )

    # ==================== Grader 应用 ====================

    def _apply_graders(
        self, case: dict, harness_output: dict, errors: list[str]
    ) -> GradeResult:
        """对 harness 输出应用 CodeGrader 和 ModelGrader。"""
        if errors:
            return GradeResult(
                passed=False, code_results=[], model_results=None, errors=errors
            )

        code_results: list[dict] = []

        # CodeGrader
        state = harness_output.get("state") or {}
        expect = case.get("expect") or {}
        agent = case.get("agent")

        if agent:
            code_results = self._grade_agent(agent, state, expect)
        else:
            # E2E case
            code_results = self.code_grader.grade_e2e(state, expect)

        code_passed = all(r["passed"] for r in code_results) if code_results else True

        # ModelGrader
        model_results = None
        rubric = case.get("rubric")
        if rubric and "transcript" in harness_output:
            model_results = harness_output.get("_model_results") or {}

        # ModelGrader 加权：correctness×0.5 + groundedness×0.35 + card_usage×0.15
        _MODEL_WEIGHTS = {"correctness": 0.5, "groundedness": 0.35, "card_usage": 0.15}
        model_passed = True
        if model_results:
            weighted = sum(
                _MODEL_WEIGHTS.get(dim) * d.get("score", 0)
                for dim, d in model_results.items()
                if _MODEL_WEIGHTS.get(dim)
            )
            model_passed = weighted >= 3.0

        passed = code_passed and model_passed

        return GradeResult(
            passed=passed,
            code_results=code_results,
            model_results=model_results,
            errors=[],
        )

    def _grade_agent(self, agent: str, state: dict, expect: dict) -> list[dict]:
        """根据 agent 类型选择对应的 CodeGrader 方法。"""
        method_map = {
            "router": self.code_grader.grade_router,
            "guard": self.code_grader.grade_guard,
            "plan": self.code_grader.grade_plan,
            "executor": self.code_grader.grade_executor,
            "qa": self.code_grader.grade_qa,
            "recommend_guard": self.code_grader.grade_recommend_guard,
            "recommend": self.code_grader.grade_recommend,
            "post_process": self.code_grader.grade_post_process,
            "user_facts": self.code_grader.grade_user_facts,
        }
        grader_fn = method_map.get(agent)
        if grader_fn:
            return grader_fn(state, expect)
        return [
            {
                "check": "unknown_agent",
                "passed": False,
                "detail": f"unknown agent type: {agent}",
            }
        ]

    # ==================== Agent Harness ====================

    async def _agent_harness(self, case: dict) -> dict:
        """Agent 单测 harness。支持 run_mode=agent、run_mode=node、summary、user_facts。"""
        agent_name = case.get("agent", "")
        setup = case.get("setup") or {}
        run_mode = setup.get("run_mode", "agent")
        overrides = setup.get("state_overrides") or {}

        if run_mode == "agent":
            return await self._run_agent_mode(agent_name, overrides)
        elif run_mode == "node":
            return await self._run_node_mode(agent_name, overrides)
        else:
            return {"state": {}, "_errors": [f"unknown run_mode: {run_mode}"]}

    async def _run_agent_mode(self, agent_name: str, overrides: dict) -> dict:
        """通过 agent.run(AgentInput(...)) 执行。"""
        from app.agents.base import AgentInput

        agent = _get_agent_singleton(agent_name)
        if agent is None:
            return {"state": {}, "_errors": [f"agent not found: {agent_name}"]}

        # 构造 AgentInput
        messages_raw = overrides.get("messages") or []
        messages = [_build_langchain_message(m) for m in messages_raw]

        thread_id = (
            overrides.get("thread_id") or f"eval_{agent_name}_{uuid.uuid4().hex[:8]}"
        )

        extra = dict(overrides.get("extra") or {})
        # Pass through common extra fields from overrides
        for key in (
            "service_key",
            "goal",
            "current_step_index",
            "slots",
            "expected_slots",
            "failed_step",
            "recommend_context",
            "skill_snapshot",
            "trace",
        ):
            if key in overrides and key not in extra:
                extra[key] = overrides[key]

        # UserFactsAgent 从 extra["messages"] 读对话
        if agent_name == "user_facts":
            extra["messages"] = messages

        input_obj = AgentInput(
            user_query=overrides.get("user_query", ""),
            user_context=overrides.get("user_context") or {},
            thread_id=thread_id,
            user_id=overrides.get("user_id"),
            extra=extra,
            messages=messages,
        )

        output = await agent.run(input_obj)

        # QAAgent/RecommendAgent 可观测：从 _result_messages 提取 tool trace
        from langchain_core.messages import AIMessage, ToolMessage

        result_messages = getattr(agent, "_result_messages", None) or []
        tool_trace = []
        for m in result_messages:
            if isinstance(m, ToolMessage):
                tool_trace.append(
                    {
                        "tool": getattr(m, "name", ""),
                        "content": str(getattr(m, "content", "")),
                    }
                )
            elif isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    tool_trace.append(
                        {
                            "tool": tc.get("name", ""),
                            "content": str(tc.get("args", {})),
                        }
                    )

        # 将 AgentOutput 转换为 state-like dict
        state = {
            "thread_id": thread_id,
            "user_id": overrides.get("user_id"),
        }
        # 合并 output.data 中的所有字段
        if output.data:
            state.update(output.data)
        if output.reply:
            state["final_reply"] = output.reply
        if output.status and output.status.value:
            status_val = output.status.value
            if status_val in ("success",):
                state["final_status"] = "success"
            elif status_val in ("failed", "timeout", "tool_error", "parsing_error"):
                state["final_status"] = "failed"

        # Agent output 字段名 → state 字段名映射
        _AGENT_TO_STATE_FIELDS = {
            "decision": "guard_decision",
        }
        for agent_field, state_field in _AGENT_TO_STATE_FIELDS.items():
            if agent_field in state and state_field not in state:
                state[state_field] = state.pop(agent_field)

        if tool_trace:
            state["_tool_trace"] = tool_trace

        return {"state": state, "output": output}

    async def _run_node_mode(self, agent_name: str, overrides: dict) -> dict:
        """通过 node 函数执行。构造 AgentState，调 node 函数，返回 Dict。"""
        node_fn = _get_node_function(agent_name)
        if node_fn is None:
            return {"state": {}, "_errors": [f"node function not found: {agent_name}"]}

        # 构造 state
        messages_raw = overrides.get("messages") or []
        messages = [_build_langchain_message(m) for m in messages_raw]
        thread_id = (
            overrides.get("thread_id")
            or f"eval_{agent_name}_node_{uuid.uuid4().hex[:8]}"
        )
        user_id = overrides.get("user_id") or "test_user_001"

        state = {
            "thread_id": thread_id,
            "user_id": user_id,
            "messages": messages,
        }
        # 合并其他 overrides（排除已处理的字段）
        skip_keys = {
            "messages",
            "user_id",
            "thread_id",
            "user_query",
            "user_context",
            "run_mode",
            "extra",
        }
        for key, value in overrides.items():
            if key not in skip_keys:
                state[key] = value

        # 设置执行上下文（SCRM 工具需要 access_token 等）
        from app.tools.business.execution_context import (
            REQUEST_ACCESS_TOKEN_CTX,
            business_execution_context,
        )

        with business_execution_context(thread_id=thread_id, user_id=user_id):
            # 设置测试用 access_token
            access_token = f"test_token_{user_id}"
            access_token_reset = REQUEST_ACCESS_TOKEN_CTX.set(access_token)
            try:
                result = await node_fn(state)
            finally:
                REQUEST_ACCESS_TOKEN_CTX.reset(access_token_reset)

        # 合并 node 返回的 state
        if isinstance(result, dict):
            state.update(result)

        return {"state": state}

    # ==================== E2E Harness ====================

    async def _e2e_user_sim_harness(self, case: dict, trial_index: int) -> dict:
        """用户模拟器 E2E：每轮 HTTP 后读 checkpoint，最后 ModelGrader。"""
        import httpx
        from .simulator import UserSimulator
        from .auth import generate_test_jwt
        from .test_data import reset_test_state

        scenario = str(case.get("scenario") or "").strip()
        if not scenario:
            return {"state": {}, "_errors": ["missing scenario"]}

        setup = case.get("setup") or {}
        user_id = setup.get("user_id", "test_user_001")
        max_turns = int(case.get("max_turns") or 20)
        task_id = case.get("task_id", "unknown")
        thread_id = f"eval_{task_id}_{trial_index}_{uuid.uuid4().hex[:8]}"

        await reset_test_state()

        sim = UserSimulator(scenario=scenario, user_id=user_id)
        token = generate_test_jwt(user_id)
        headers = {"Authorization": f"Bearer {token}"}

        current_msg = await sim.start_conversation()
        per_turn_states: list[dict] = []
        final_state: dict = {}

        async with httpx.AsyncClient(
            base_url="http://backend:8000", timeout=120.0
        ) as client:
            for turn_no in range(max_turns):
                t0 = time.perf_counter()
                resp = await client.post(
                    "/api/v1/chat",
                    json={
                        "message": current_msg,
                        "thread_id": thread_id,
                        "channel": "eval",
                    },
                    headers=headers,
                )
                response_ms = int((time.perf_counter() - t0) * 1000)

                if resp.status_code != 200:
                    return {
                        "state": {"_http_status": resp.status_code},
                        "_errors": [f"HTTP {resp.status_code}: {resp.text[:200]}"],
                    }

                reply = (resp.json().get("reply") or "").strip()
                if not reply:
                    break

                snap = await _load_final_state(thread_id)
                per_turn_states.append(snap)

                resp_data = resp.json()
                reply = (resp_data.get("reply") or "").strip()
                if not reply:
                    break
                interaction = resp_data.get("interaction")
                products = resp_data.get("products") or []

                # 拼装结构化 JSON 给模拟器（不是自然语言描述）
                full_response = json.dumps(
                    {
                        "reply": reply,
                        "card": _compact_interaction(interaction),
                        "products": _compact_products(products),
                    },
                    ensure_ascii=False,
                )

                snap = await _load_final_state(thread_id)
                per_turn_states.append(snap)

                print(f"\n--- Turn {turn_no + 1} ({response_ms}ms) ---")
                print(f"[USER] {current_msg}")
                print(f"[AI]   {full_response}")
                state_keys = {
                    k: v for k, v in snap.items() if v not in (None, [], {}, "", 0)
                }
                print(
                    f"[STATE] {json.dumps(state_keys, ensure_ascii=False, default=str)[:300]}"
                )

                next_msg = await sim.next_message(full_response)
                if next_msg is None:
                    break
                current_msg = next_msg

        final_state = await _load_final_state(thread_id)
        merged_state = _merge_checkpoint_snapshots(per_turn_states, final_state)

        model_results = {}
        rubric = case.get("rubric") or []
        if rubric:
            model_results = await self.model_grader.grade(
                rubric=rubric,
                transcript=sim.transcript,
                scenario=scenario,
                reference=case.get("reference", ""),
            )

        _write_e2e_log(task_id, trial_index, thread_id, model_results)

        return {
            "state": merged_state,
            "transcript": sim.transcript,
            "_model_results": model_results,
            "_per_turn_states": per_turn_states,
        }

    async def _e2e_fixed_harness(self, case: dict, trial_index: int) -> dict:
        """固定 case E2E：逐轮 HTTP，每轮读 checkpoint。"""
        import httpx
        from .auth import generate_test_jwt

        setup = case.get("setup") or {}
        user_id = setup.get("user_id", "test_user_001")
        task_id = case.get("task_id", "unknown")
        thread_id = f"eval_{task_id}_{trial_index}_{uuid.uuid4().hex[:8]}"
        turns = case.get("turns") or []

        token = generate_test_jwt(user_id)
        headers = {"Authorization": f"Bearer {token}"}
        transcript: list[dict] = []
        per_turn_states: list[dict] = []
        final_state: dict = {}

        async with httpx.AsyncClient(
            base_url="http://backend:8000", timeout=120.0
        ) as client:
            for turn_no, turn in enumerate(turns):
                role = turn.get("role", "user")
                if role != "user":
                    continue
                content = turn.get("content", "")
                resp = await client.post(
                    "/api/v1/chat",
                    json={
                        "message": content,
                        "thread_id": thread_id,
                        "channel": "eval",
                    },
                    headers=headers,
                )
                resp_data = resp.json() if resp.status_code == 200 else {}
                reply = resp_data.get("reply", f"HTTP {resp.status_code}")
                interaction = resp_data.get("interaction")
                products = resp_data.get("products") or []

                full_response = json.dumps(
                    {
                        "reply": reply,
                        "card": _compact_interaction(interaction),
                        "products": _compact_products(products),
                    },
                    ensure_ascii=False,
                )

                transcript.append({"role": "user", "content": content})
                transcript.append({"role": "assistant", "content": full_response})

                per_turn_states.append(await _load_final_state(thread_id))
        final_state = await _load_final_state(thread_id)
        merged_state = _merge_checkpoint_snapshots(per_turn_states, final_state)
        merged_state["_http_status"] = 200

        model_results = {}
        rubric = case.get("rubric") or []
        if rubric:
            model_results = await self.model_grader.grade(
                rubric=rubric,
                transcript=transcript,
                scenario=case.get("description", ""),
                reference=case.get("reference", ""),
            )

        _write_e2e_log(task_id, trial_index, thread_id, model_results)

        return {
            "state": merged_state,
            "transcript": transcript,
            "_model_results": model_results,
            "_per_turn_states": per_turn_states,
        }


# ---- 辅助函数 ----


def _get_agent_singleton(agent_name: str):
    """根据 agent 名称获取单例。"""
    mapping = {
        "router": "app.agents.router_agent.router_agent",
        "guard": "app.agents.ticket.guard_agent.ticket_guard_agent",
        "plan": "app.agents.ticket.plan_agent.ticket_plan_agent",
        "qa": "app.agents.qa_agent.qa_agent",
        "recommend": "app.agents.recommend_agent.recommend_agent",
        "recommend_guard": "app.agents.recommend_guard_agent.recommend_guard_agent",
        "user_facts": "app.agents.user_facts_agent.user_facts_agent",
    }
    path = mapping.get(agent_name)
    if not path:
        return None
    module_path, attr = path.rsplit(".", 1)
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, attr)


def _get_node_function(agent_name: str):
    """根据 agent 名称获取对应的 node 函数。"""
    mapping = {
        "router": "app.workflow.nodes.router.router.router_node",
        "guard": "app.workflow.nodes.ticket.guard.guard_node",
        "plan": "app.workflow.nodes.ticket.planner.plan_node",
        "executor": "app.workflow.nodes.ticket.executor.executor_node",
        "qa": "app.workflow.nodes.qa.qa.qa_node",
        "recommend": "app.workflow.nodes.recommend.recommend.recommend_node",
    }
    path = mapping.get(agent_name)
    if not path:
        return None
    module_path, attr = path.rsplit(".", 1)
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, attr)


def _compact_interaction(interaction: dict | None) -> dict | None:
    """精简 interaction 卡片，只保留模拟器需要的字段。"""
    if not interaction or not isinstance(interaction, dict):
        return None
    result = {"type": interaction.get("interaction_type")}
    items = interaction.get("items") or []
    compact_items = []
    for it in items:
        if not isinstance(it, dict):
            continue
        ci = {"label": it.get("label", ""), "key": it.get("key", "")}
        detail = it.get("detail") or {}
        if isinstance(detail, dict):
            ci["detail"] = {
                k: v
                for k, v in detail.items()
                if v
                and k
                in (
                    "order_id",
                    "ticket_id",
                    "ticket_type",
                    "ticket_title",
                    "product_id",
                    "product_name",
                    "ticket_status_label",
                    "order_status_label",
                    "items_preview",
                )
            }
        compact_items.append(ci)
    result["items"] = compact_items
    return result


def _compact_products(products: list) -> list:
    """精简产品列表，只保留关键字段。"""
    if not products:
        return []
    compact = []
    for p in products[:5]:
        if not isinstance(p, dict):
            continue
        compact.append(
            {
                "name": p.get("name", ""),
                "price": p.get("price", ""),
                "product_id": p.get("product_id", ""),
                "color_name": p.get("color_name", ""),
                "category": p.get("category", ""),
                "gender": p.get("gender", ""),
            }
        )
    return compact


def _write_e2e_log(
    task_id: str, trial: int, thread_id: str, model_results: dict
) -> None:
    """写 E2E 评分结果到 tests/eval/logs/。只记评分，对话|token|trace 由 Langfuse 管理。"""
    import json

    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{task_id}_trial{trial}_{ts}.json"
    data = {
        "task_id": task_id,
        "trial": trial,
        "thread_id": thread_id,
        "scores": model_results,
    }
    (log_dir / filename).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


async def _load_final_state(thread_id: str) -> dict:
    """直接从 Redis checkpointer 加载 checkpoint state。"""
    try:
        from app.memory.redis_checkpointer import create_checkpointer

        ck = await create_checkpointer()
        saved = await ck.aget_tuple({"configurable": {"thread_id": thread_id}})
        if saved and hasattr(saved, "checkpoint"):
            cp = saved.checkpoint
            vals = cp.get("channel_values", {}) if isinstance(cp, dict) else {}
            return vals if isinstance(vals, dict) else {}
        return {}
    except Exception:
        return {}


def _merge_checkpoint_snapshots(per_turn_states: list[dict], final_state: dict) -> dict:
    """合并多轮 checkpoint snapshot：最后一轮有数据的覆盖前面的。

    服务结束后 state 会被 clean，所以靠中间 snapshot 保留 steps/try_process 等。
    """
    merged: dict = {}
    for snap in per_turn_states:
        for key, value in snap.items():
            if (
                value is not None
                and value != []
                and value != {}
                and value != ""
                and value != 0
            ):
                merged[key] = value
    # final_state 覆盖（如果没被清的话）
    for key, value in final_state.items():
        if (
            value is not None
            and value != []
            and value != {}
            and value != ""
            and value != 0
        ):
            merged[key] = value
    return merged


def _build_langchain_message(raw: dict):
    """将 case JSON 中的消息 dict 转为 LangChain message 对象。"""
    role = raw.get("role", "user")
    content = raw.get("content", "")

    if role == "human" or role == "user":
        return HumanMessage(content=content)
    elif role == "ai" or role == "assistant":
        from langchain_core.messages import AIMessage

        return AIMessage(content=content)
    elif role == "tool":
        from langchain_core.messages import ToolMessage

        return ToolMessage(
            content=content,
            tool_call_id=raw.get("tool_call_id", "call_0"),
            name=raw.get("name", ""),
        )
    else:
        return HumanMessage(content=content)
