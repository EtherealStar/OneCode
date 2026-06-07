"""Prompt text for the write_file tool."""

PROMPT = """Create or completely overwrite a sandboxed text file.

Use this tool when creating a new file or replacing the entire contents of an existing file.
Prefer edit_file for localized changes because it sends a smaller, safer diff.
For existing files, read the file first in this session before using write_file.
Do not create documentation files such as *.md or README files unless the user explicitly asks for them.
Avoid emojis in written files unless the user explicitly asks for them.
"""
