PROMPT = """# Tool: background_task_stop

Stop an in-process background execution task by id.

Rules:
- Use this only for background tasks started by `bash` or `agent` with `run_in_background=true`, or for internal dream tasks if the user explicitly asks to stop one.
- This does not stop durable Todo tasks from `task_create`, `task_get`, `task_update`, or `task_list`.
- If the task is already completed, failed, or killed, the tool reports the current terminal state.
"""
