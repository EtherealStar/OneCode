PROMPT = """Purpose:
Update a durable task in the current task list.

Use when:
- You need to change a task subject, description, active form, status, owner, metadata, or dependency edges.
- You need to mark a task completed or deleted.

Prefer instead:
- Use `task_list` or `task_get` first if you are unsure about the target task id or current state.
- Use `background_task_stop` for running background executions; this tool only updates durable task records.

Rules:
- Pass the exact `taskId`.
- Use `status="completed"` only when the task is genuinely done.
- Use `status="deleted"` to delete a durable task record.
- `addBlocks` means this task blocks the listed tasks; `addBlockedBy` means this task is blocked by the listed tasks.
- Metadata is merged by the task store; keep it small and structured.

Returns:
- An update summary with changed fields, status, task id, and task list id.

If it fails:
- If the task is not found, list tasks or inspect the intended id before retrying.
- If completion is blocked by a hook, address the reported blocker before marking complete.
"""
