"""System prompt guidance for the skill tool."""

PROMPT = """Purpose:
Load and apply a visible OneCode skill by name.

Use when:
- The user explicitly names a skill or invokes one with `/<name>`.
- The task clearly matches an Available Skill description or when-to-use text.

Rules:
- Call this tool before doing work that depends on the skill instructions.
- Do not call the same skill again after it has already been loaded in the current conversation.
- Pass concise `args` when the skill needs task-specific context.
- After the tool returns, continue the user task using the loaded skill instructions.

Returns:
- Inline skills return a launch message and add the skill instructions to subsequent context.
- Fork skills return JSON with child agent details and final text.

If it fails:
- If the skill is unknown or not model-invocable, proceed without it and explain the limitation if it affects the task.
"""
