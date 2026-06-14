"""Prompt text for the edit_file tool."""

PROMPT = """Purpose:
Edit a UTF-8 text file by replacing an exact string with another string.

Use when:
- You need a small, targeted change in an existing file.
- You can provide enough exact surrounding text to identify the intended replacement.

Prefer instead:
- Use `write_file` when the entire file should be replaced or created from scratch.
- Use `read_file` first if you do not have the exact current text.

Rules:
- Existing files must be read in this session before editing.
- `old_string` must match the current file content exactly.
- When a match is not unique, provide more surrounding context or set `replace_all=true` only if every occurrence should change.
- A missing file can only be created by passing an empty `old_string`.

Returns:
- A summary with the edited path and replacement count.

If it fails:
- If `old_string` is not found or has multiple matches, read the relevant lines and retry with a more precise string.
- If the file has not been read, read it first and then retry.
"""
