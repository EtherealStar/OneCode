PROMPT = """Purpose:
Update a durable task in the current task list.

Use when:
- You need to change a task subject, description, active form, status, owner, metadata, or dependency edges.
- You need to mark a task completed or deleted.
- You are starting, pausing, unblocking, or finishing durable work.

Prefer instead:
- Use `task_list` or `task_get` first if you are unsure about the target task id or current state.
- Use `background_task_stop` for running background executions; this tool only updates durable task records.

Rules:
- Pass the exact `taskId`.
- Set `status="in_progress"` when you start substantial work on a pending task.
- Use `status="completed"` only when the task is genuinely done.
- If work is partial, blocked, or verification failed, keep the task open and record the blocker or follow-up instead of completing it.
- Use `status="deleted"` to delete a durable task record.
- `addBlocks` means this task blocks the listed tasks; `addBlockedBy` means this task is blocked by the listed tasks.
- Metadata is merged by the task store; keep it small and structured.

Returns:
- An update summary with changed fields, status, task id, and task list id.

If it fails:
- If the task is not found, list tasks or inspect the intended id before retrying.
- If completion is blocked by a hook, address the reported blocker before marking complete.
"""
