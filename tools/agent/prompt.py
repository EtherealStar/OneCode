"""Model-facing prompt for the agent tool."""

PROMPT = """Purpose:
Delegate a bounded subtask to a built-in subagent and receive its final summary.

Use when:
- A subtask is separable from the main line of work and benefits from isolated investigation.
- You need clean-context research, read-only exploration, or a focused plan after inspection.

Rules:
- Keep the delegated prompt specific and bounded; include the goal, relevant files or constraints, and expected output.
- Omit `subagent_type` to fork from the current parent context.
- Use `subagent_type="general-purpose"` for clean-context complex research.
- Use `subagent_type="Explore"` for read-only code search and inspection.
- Use `subagent_type="Plan"` for read-only planning after code inspection.
- With `run_in_background=true`, the tool starts a `local_agent` background task and returns immediately with an `a_...` task id and output file. Completion is reported as a `<task_notification>` only on the user's next input.

Returns:
- Synchronous mode returns JSON with agent type, child session id, transition, tool result count, and final text.
- Background mode returns task id, task type, status, agent type, and output file.

If it fails:
- Narrow the delegated task, choose a more appropriate subagent type, or continue directly in the parent context.
"""
