"""Thin agent lifecycle loop."""

from __future__ import annotations

from core.context_engine import ContextEngine
from core.runtime_state import RuntimeState
from core.transitions import TransitionReason
from services.context.message_store import MessageStore
from services.model.client import ModelClient
from services.tools.executor import ToolExecutor


class AgentLoop:
    def __init__(
        self,
        *,
        state: RuntimeState,
        message_store: MessageStore,
        context_engine: ContextEngine,
        model_client: ModelClient,
        tool_executor: ToolExecutor,
    ) -> None:
        self.state = state
        self.message_store = message_store
        self.message_store.bind_session(self.state.session_id)
        self.context_engine = context_engine
        self.model_client = model_client
        self.tool_executor = tool_executor

    def run(self, prompt: str) -> str:
        self.message_store.append_user(prompt)
        return self.run_loop()

    def run_loop(self) -> str:
        while True:
            self.state.turn_count += 1
            if self.state.turn_count > self.state.max_turns:
                self.state.set_transition(TransitionReason.MAX_TURNS)
                return "Stopped: maximum turn count reached."

            # 主循环保持薄：上下文、prompt 和工具 schema 都交给
            # ContextEngine 每轮重建，以反映最新运行时状态。
            snapshot = self.context_engine.build_for_model(self.state)
            response = self.model_client.send(snapshot)

            if response.usage is not None:
                self.state.add_usage(response.usage)

            self.message_store.append_assistant(response.assistant_message)

            # 是否继续执行工具取决于实际 tool_calls，而不是 provider 私有的
            # stop reason 字段。
            if response.tool_calls:
                result_blocks = self.tool_executor.execute(
                    response.tool_calls,
                    self.state,
                )
                self.message_store.append_tool_results(result_blocks)
                self.state.set_transition(TransitionReason.TOOL_USE)
                continue

            self.state.set_transition(TransitionReason.COMPLETED)
            return response.final_text
