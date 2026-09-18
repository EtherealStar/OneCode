"""enter_plan_mode 工具的提示词文本。

模型读取该提示词以了解何时适合进入计划模式。其精神与 Claude Code 的工具提示词一致，
同时保持 OneCode 简明的风格。
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