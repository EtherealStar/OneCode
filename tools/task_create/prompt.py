PROMPT = """Purpose:
Create a durable task in the current task list.

Use when:
- The user wants work tracked across turns or sessions.
- You need to record a concrete follow-up item, coordination item, or recoverable unit of work.

Prefer instead:
- Use normal conversation or a short plan for ephemeral steps inside the current turn.
- Use `task_update` to add dependencies after a task exists.

Rules:
- Write a concise subject and a description that is specific enough to resume later.
- Use `activeForm` for the current actionable phrasing when it differs from the stable subject.
- Metadata should be small, structured, and relevant.

Returns:
- A creation summary with task id and task list metadata.

If it fails:
- If task storage rejects the request, simplify the task fields or report the storage error.
"""
