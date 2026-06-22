"""Prompt text for the ``enter_plan_mode`` tool.

The model reads this to understand when entering plan mode is appropriate. It
matches the spirit of Claude Code's tool prompt while keeping OneCode's voice
concise.
"""

PROMPT = """\
Use this tool proactively when you're about to start a non-trivial
implementation task. Entering plan mode lets you investigate the codebase,
interview the user about their requirements, and write a structured plan to
``<workspace>/.onecode/plans/<slug>.md`` for review before any code changes
are made.

When to use this tool:

- Complex multi-file changes where the design has multiple viable approaches
- Bug investigations that may end up rewriting several modules
- Tasks where the user has not specified file paths or scope yet
- Refactors that touch shared infrastructure (permissions, subagents,
  attachments, executor)

When NOT to use this tool:

- Single-file edits with obvious scope ("rename this function")
- Pure read-only research or Q&A tasks
- The user already approved a detailed plan in chat

If you are already in plan mode, calling this tool again is a no-op and
returns the current plan file path.

After entering plan mode, only the following tool families are allowed:

- Read-only exploration: ``read_file``, ``glob``, ``grep``, ``bash`` with
  read-only commands, ``agent`` with ``subagent_type="explore"``
- Plan file writes: ``write_file`` and ``edit_file`` targeting the plan file
- Plan workflow: ``ask_user_question``, ``exit_plan_mode``

End your turn by either calling ``exit_plan_mode`` to submit the plan or
asking the user a clarifying question with ``ask_user_question``.
"""