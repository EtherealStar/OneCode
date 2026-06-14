PROMPT = """Purpose:
Stop a running background execution task by id.

Use when:
- The user asks to stop a background task started by `bash` or `agent` with `run_in_background=true`.
- The user explicitly asks to stop an internal dream task.

Prefer instead:
- Use `task_update` to change durable task records; this tool does not stop or delete durable tasks.

Rules:
- Only pass a background task id such as `b_...`, `a_...`, or `d_...`.
- If the task is already completed, failed, or killed, the tool reports its current terminal state.

Returns:
- JSON with task id, task type, status, and output file.

If it fails:
- If the task id is not found, check the latest background task listing or ask the user for the correct id.
"""
