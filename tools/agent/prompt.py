"""Model-facing prompt for the agent tool."""

PROMPT = """# Tool: agent

Delegate a bounded subtask to a built-in subagent and wait for its final
summary. The subagent's intermediate messages are not added to the parent
conversation.

- Omit `subagent_type` to fork from the current parent context.
- Use `subagent_type="general-purpose"` for clean-context complex research.
- Use `subagent_type="Explore"` for read-only code search and inspection.
- Use `subagent_type="Plan"` for read-only planning after code inspection.
- Do not pass `run_in_background`; this runtime waits for the child result.
"""
