"""Model-facing prompt for the agent tool."""

PROMPT = """# Tool: agent

Delegate a bounded subtask to a built-in subagent. By default the tool waits
for the final summary. With `run_in_background=true`, it starts a `local_agent`
background task and returns immediately with an `a_...` task id and output file.
Completion is reported as a `<task_notification>` only on the user's next input.

- Omit `subagent_type` to fork from the current parent context.
- Use `subagent_type="general-purpose"` for clean-context complex research.
- Use `subagent_type="Explore"` for read-only code search and inspection.
- Use `subagent_type="Plan"` for read-only planning after code inspection.
"""
