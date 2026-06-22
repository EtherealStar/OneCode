"""Prompt text for the ``exit_plan_mode`` tool."""

PROMPT = """\
Use this tool when you are in plan mode and have finished writing the plan to
``.onecode/plans/<slug>.md``. Calling it asks the user to approve the plan
and, on approval, returns the runtime to its pre-plan permission mode so
implementation can begin.

Before using this tool:

- Make sure the plan file is complete and reflects your final intent.
- Stop editing the plan file after you call this tool.
- Do NOT use ``ask_user_question`` as a substitute for plan approval. The
  user reviews the plan in a dedicated approval surface, not through
  questions.

If the user rejects the plan, you will remain in plan mode. Read the
``rejection_feedback`` returned by this tool, adjust the plan, and call
``exit_plan_mode`` again.

Do not use this tool for:

- Pure research tasks that never entered plan mode
- Tasks where you only need to ask the user a clarifying question
  (use ``ask_user_question`` instead)
"""
