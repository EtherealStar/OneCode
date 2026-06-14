PROMPT = """Purpose:
Inspect the full details for one durable task.

Use when:
- You need a task description, active form, owner, status, dependencies, or metadata before acting.
- A compact `task_list` entry is not enough context.

Prefer instead:
- Use `task_list` first when you do not know the task id.

Rules:
- Pass the exact task id from the current task list.

Returns:
- Pretty-printed JSON for the task when found.
- A not-found result when the id does not exist in the current task list.

If it fails:
- Use `task_list` to confirm the task id, then retry if needed.
"""
