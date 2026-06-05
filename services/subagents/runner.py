"""Subagent runner composition."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

from core.context_engine import ContextEngine, StaticPromptAssembler
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.guard import SandboxGuard
from services.model.client import ModelClient
from services.observability import TraceRecorder
from services.permissions import PermissionPolicy, PermissionPrompter
from services.subagents.context import CurrentModelContext
from services.subagents.definitions import get_agent_definition
from services.subagents.forking import build_forked_messages
from services.subagents.types import AgentDefinition, SubagentRequest, SubagentResult
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import ToolDescriptor


class SubagentRunner:
    def __init__(
        self,
        *,
        workspace: Path,
        transcript_root: Path,
        parent_message_store: MessageStore,
        current_model_context: CurrentModelContext,
        model_client: ModelClient,
        base_descriptors: tuple[ToolDescriptor, ...],
        guard: SandboxGuard,
        permission_policy: PermissionPolicy,
        permission_prompter: PermissionPrompter | None,
        trace_recorder: TraceRecorder,
    ) -> None:
        self._workspace = workspace
        self._transcript_root = transcript_root
        self._parent_message_store = parent_message_store
        self._current_model_context = current_model_context
        self._model_client = model_client
        self._base_descriptors = base_descriptors
        self._guard = guard
        self._permission_policy = permission_policy
        self._permission_prompter = permission_prompter
        self._trace_recorder = trace_recorder

    def bind_parent_message_store(self, message_store: MessageStore) -> None:
        """Rebind fork source messages after CLI resume or session clear."""

        self._parent_message_store = message_store

    async def run(self, request: SubagentRequest) -> SubagentResult:
        """Run one child loop and collapse its work into a final summary."""

        definition = self._definition_for_request(request)
        if definition is None:
            return self._error_result(
                agent_type=request.subagent_type or "unknown",
                message=f"Unknown subagent_type: {request.subagent_type}",
                error="unknown_subagent",
            )
        is_fork = request.subagent_type is None
        child_state = RuntimeState(max_turns=definition.max_turns or 20)
        child_state.metadata["hidden_tools"] = {"agent"}
        if definition.read_only:
            child_state.metadata["read_only_agent"] = True
        if is_fork:
            child_state.metadata["is_fork_child"] = True
        child_store = MessageStore(
            transcript_root=self._transcript_root,
            session_id=child_state.session_id,
            cwd=self._workspace,
        )
        seed_result = self._seed_child_messages(
            child_store,
            definition,
            request,
            is_fork=is_fork,
        )
        if seed_result is not None:
            return seed_result

        registry = ToolRegistry(
            _child_descriptors(definition, self._base_descriptors),
            permission_policy=self._permission_policy,
        )
        prompt_assembler = self._prompt_assembler(definition, is_fork=is_fork)
        context_engine = ContextEngine(
            child_store,
            prompt_assembler=prompt_assembler,
            tool_schema_provider=registry,
        )
        tool_executor = RegistryToolExecutor(
            registry,
            guard=self._guard,
            permission_policy=self._permission_policy,
            permission_prompter=self._permission_prompter,
            trace_recorder=self._trace_recorder,
        )
        loop = AgentLoop(
            state=child_state,
            message_store=child_store,
            context_engine=context_engine,
            model_client=self._model_client,
            tool_executor=tool_executor,
            trace_recorder=self._trace_recorder,
        )
        return await self._drain_loop(
            loop,
            child_store,
            child_state,
            definition,
            request,
            is_fork=is_fork,
        )

    def _definition_for_request(
        self,
        request: SubagentRequest,
    ) -> AgentDefinition | None:
        # Omitted subagent_type is the explicit fork signal for the first version.
        return get_agent_definition(request.subagent_type or "fork")

    def _seed_child_messages(
        self,
        child_store: MessageStore,
        definition: AgentDefinition,
        request: SubagentRequest,
        *,
        is_fork: bool,
    ) -> SubagentResult | None:
        # Seed before continuing the child loop so fork does not duplicate prompts.
        if not is_fork:
            child_store.seed_messages(({"role": "user", "content": request.prompt},))
            return None
        snapshot = self._current_model_context.snapshot
        if snapshot is None:
            return self._error_result(
                agent_type=definition.agent_type,
                message="Fork subagent requires the parent model snapshot.",
                error="fork_context_unavailable",
            )
        forked_messages = build_forked_messages(
            self._parent_message_store.current_messages(),
            request.prompt,
        )
        child_store.seed_messages(forked_messages)
        return None

    def _prompt_assembler(
        self,
        definition: AgentDefinition,
        *,
        is_fork: bool,
    ) -> StaticPromptAssembler:
        # Fork must inherit the exact bytes already rendered for the parent turn.
        if is_fork:
            snapshot = self._current_model_context.snapshot
            return StaticPromptAssembler(snapshot.system_prompt if snapshot else "")
        return StaticPromptAssembler(definition.system_prompt)

    async def _drain_loop(
        self,
        loop: AgentLoop,
        child_store: MessageStore,
        child_state: RuntimeState,
        definition: AgentDefinition,
        request: SubagentRequest,
        *,
        is_fork: bool,
    ) -> SubagentResult:
        started = perf_counter()
        self._trace_recorder.event(
            "subagent_start",
            {
                "agent_type": definition.agent_type,
                "parent_session_id": request.parent_session_id,
                "child_session_id": child_state.session_id,
                "is_fork": is_fork,
                "read_only": definition.read_only,
                "prompt_length": len(request.prompt),
            },
        )
        final_text = ""
        try:
            async for event in loop.continue_stream():
                if event.type == "completed":
                    final_text = event.text
        except Exception as exc:
            child_store.flush_transcript()
            self._trace_recorder.event(
                "subagent_error",
                {
                    "agent_type": definition.agent_type,
                    "child_session_id": child_state.session_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            return self._error_result(
                agent_type=definition.agent_type,
                session_id=child_state.session_id,
                message=f"Subagent failed: {type(exc).__name__}: {exc}",
                error="subagent_error",
                transition=(
                    child_state.last_transition.value
                    if child_state.last_transition is not None
                    else None
                ),
            )
        result = SubagentResult(
            agent_type=definition.agent_type,
            session_id=child_state.session_id,
            final_text=final_text,
            transition=(
                child_state.last_transition.value
                if child_state.last_transition is not None
                else None
            ),
            usage=replace(child_state.usage),
            tool_result_count=_tool_result_count(child_store),
            metadata={"is_fork": is_fork, "read_only": definition.read_only},
        )
        child_store.flush_transcript()
        self._trace_recorder.event(
            "subagent_completed",
            {
                "agent_type": result.agent_type,
                "child_session_id": result.session_id,
                "transition": result.transition,
                "tool_result_count": result.tool_result_count,
                "input_tokens": result.usage.input_tokens if result.usage else 0,
                "output_tokens": result.usage.output_tokens if result.usage else 0,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            },
        )
        return result

    def _error_result(
        self,
        *,
        agent_type: str,
        message: str,
        error: str,
        session_id: str = "",
        transition: str | None = None,
    ) -> SubagentResult:
        return SubagentResult(
            agent_type=agent_type,
            session_id=session_id,
            final_text=message,
            is_error=True,
            transition=transition,
            metadata={"error": error},
        )


def _child_descriptors(
    definition: AgentDefinition,
    base_descriptors: tuple[ToolDescriptor, ...],
) -> tuple[ToolDescriptor, ...]:
    allowed_names = set(definition.tools)
    disallowed = set(definition.disallowed_tools)
    disallowed.add("agent")
    descriptors: list[ToolDescriptor] = []
    for descriptor in base_descriptors:
        if descriptor.name in disallowed:
            continue
        if "*" not in allowed_names and descriptor.name not in allowed_names:
            continue
        descriptors.append(descriptor)
    return tuple(descriptors)


def _tool_result_count(message_store: MessageStore) -> int:
    return sum(
        1
        for message in message_store.current_messages()
        if message.get("role") == "tool_result"
    )
