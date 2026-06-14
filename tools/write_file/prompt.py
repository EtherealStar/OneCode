"""Prompt text for the write_file tool."""

PROMPT = """Purpose:
Create a new UTF-8 text file or replace the complete contents of an existing text file.

Use when:
- You are creating a new file.
- You intentionally need to rewrite an entire existing file.

Prefer instead:
- Use `edit_file` for localized changes to existing files.
- Use `read_file` first when you need the current contents.

Rules:
- Existing files must be fully read in this session before they can be overwritten.
- Treat this as a whole-file operation; include the complete desired file content.
- Do not create documentation files such as README or `*.md` unless the user asked for documentation.
- Avoid emoji or decorative content in generated files unless the user explicitly asks for it.

Returns:
- A creation or update summary.
- For updates, a unified diff preview may be included and may be truncated.

If it fails:
- If the file was not read, read it fully and retry only after confirming the replacement is still appropriate.
- If the file changed after it was read, read it again before overwriting.
"""
