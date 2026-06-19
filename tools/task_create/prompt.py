PROMPT = """Purpose:
Create a durable task in the current task list.

Use when:
- The user wants work tracked across turns or sessions.
- The work has several distinct steps, multiple requested items, dependencies, or meaningful follow-up items.
- You need to record a concrete coordination item, blocker, or recoverable unit of work.

Prefer instead:
- Use normal conversation or a short plan for a single straightforward change, one-off lookup, or ephemeral steps inside the current turn.
- Use `task_list` first when an existing task list may already cover the work.
- Use `task_update` to add dependencies after a task exists.

Rules:
- Write a concise subject and a description that is specific enough to resume later.
- Describe the desired outcome, not every mechanical step you expect to take.
- Use `activeForm` for the current actionable phrasing when it differs from the stable subject.
- Metadata should be small, structured, and relevant.

Returns:
- A creation summary with task id and task list metadata.

If it fails:
- If task storage rejects the request, simplify the task fields or report the storage error.
"""
