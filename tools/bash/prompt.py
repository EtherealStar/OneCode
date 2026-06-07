PROMPT = """# Tool: bash

Execute a Git Bash command in the current workspace.

Rules:
- Use this for shell inspection and small command-line workflows, not as a replacement for dedicated file tools when `read_file`, `edit_file`, `glob`, or `grep` are more precise.
- Commands are parsed with Tree-sitter before execution. Simple commands, top-level `&&`, `||`, `;`, and pipelines are supported.
- Complex shell language such as subshells, command substitution, heredocs, process substitution, loops, functions, and conditionals is not auto-approved.
- Read-only commands such as `git status`, `git diff`, `ls`, `cat`, `rg`, and `grep` can run automatically when their paths stay inside the sandbox.
- Commands that write, delete, execute unknown programs, or have unclear side effects require permission before execution.
- For slow commands, set `run_in_background=true`. The tool returns immediately with a `b_...` task id and an output file under `.onecode/<session>/background-tasks/`; completion is reported as a `<task_notification>` only on the user's next input.
- Git Bash must be available on PATH or in a standard Git for Windows install location.
"""
