PROMPT = """Purpose:
List visible durable tasks in the current task list.

Use when:
- You need to inspect outstanding, in-progress, completed, or blocked durable work.
- You need task ids before calling `task_get` or `task_update`.

Rules:
- Use this before updating a task when you do not know the exact task id.
- Completed blockers are omitted from the blocked summary.

Returns:
- A compact task list with id, status, subject, owner, and unfinished blockers when present.

If it fails:
- Report the task store error and avoid guessing task ids.
"""
