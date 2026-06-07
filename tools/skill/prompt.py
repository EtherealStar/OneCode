"""System prompt guidance for the skill tool."""

PROMPT = """Use the skill tool before doing work that matches an Available Skill.

- Call skill with {"skill": "<name>"} when the user explicitly names a skill or uses /<name>.
- Call skill when a task clearly matches a skill's description or when_to_use text.
- Do not call the same skill again after it has already been loaded in the current conversation.
- After the tool returns, continue the task using the loaded skill instructions."""
