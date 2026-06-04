PROMPT = """# Tool: bash

Execute a Git Bash command in the current workspace.

Rules:
- Use this for shell inspection and small command-line workflows, not as a replacement for dedicated file tools when `read_file`, `edit_file`, `glob`, or `grep` are more precise.
- Commands are parsed with Tree-sitter before execution. Simple commands, top-level `&&`, `||`, `;`, and pipelines are supported.
- Complex shell language such as subshells, command substitution, heredocs, process substitution, loops, functions, and conditionals is not auto-approved.
- Read-only commands such as `git status`, `git diff`, `ls`, `cat`, `rg`, and `grep` can run automatically when their paths stay inside the sandbox.
- Commands that write, delete, execute unknown programs, or have unclear side effects require permission before execution.
- Git Bash must be available on PATH or in a standard Git for Windows install location.
"""
