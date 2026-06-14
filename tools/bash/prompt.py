PROMPT = """Purpose:
Execute a Git Bash command in the current workspace.

Use when:
- You need command-line inspection, test execution, build checks, git status, or a small shell workflow.
- A dedicated file or search tool is not precise enough for the task.

Prefer instead:
- Use `read_file`, `edit_file`, `write_file`, `glob`, or `grep` for direct file inspection and edits.
- Use task tools for durable project work tracking instead of shell notes.

Rules:
- Commands run through Git Bash; Git Bash must be installed or available on PATH.
- Commands are parsed before execution. Simple commands, top-level `&&`, `||`, `;`, and pipelines are supported.
- Complex shell language such as subshells, command substitution, heredocs, process substitution, loops, functions, and conditionals is treated conservatively.
- Read-only commands such as `git status`, `git diff`, `ls`, `cat`, `rg`, and `grep` may run automatically when their paths stay within allowed project areas.
- Commands that write, delete, execute unknown programs, or have unclear side effects may require permission before execution.
- For slow commands, set `run_in_background=true`. The tool returns immediately with a `b_...` task id and an output file under `.onecode/<session>/background-tasks/`; completion is reported as a `<task_notification>` only on the user's next input.

Returns:
- The command, exit code, stdout, stderr, timeout status, and any command-specific interpretation.
- Background mode returns task id, task type, status, command, and output file.

If it fails:
- If Git Bash is missing, report that requirement and use non-shell tools where possible.
- If permission or parsing blocks the command, simplify it, use a dedicated tool, or ask the user before trying a broader command.
"""
