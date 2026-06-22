"""Prompt text for the ``ask_user_question`` tool."""

PROMPT = """\
Use this tool when you need structured input from the user before continuing.

Each question must:

- Have a short, specific ``question`` text (under 12 words is ideal).
- Provide 2 to 4 mutually exclusive options via ``options``. Each option has
  a ``label`` (1 to 5 words) and an optional ``description`` (a sentence of
  context that helps the user choose).
- Provide a short ``header`` (max 12 chars) used in compact UI surfaces.
- Set ``multi_select: true`` only when the user can sensibly pick several
  options at once.

Use this tool for:

- Choosing between clearly distinct design directions.
- Picking one option from a list of equally valid trade-offs.
- Confirming scope or product decisions the user must make.

Do NOT use this tool for:

- Asking the user to approve or reject the current plan. Plan approval is
  handled by ``exit_plan_mode``.
- Open-ended freeform questions with no reasonable options.
- Confirmation of an action whose outcome is already determined.

If the user declines to answer, the tool returns a structured "declined"
result; treat that as "ask again later" or pick a safe default and explain
your choice in the plan file.
"""
