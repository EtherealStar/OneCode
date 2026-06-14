"""Prompt text for the read_file tool."""

PROMPT = """Purpose:
Read a UTF-8 text file and return line-numbered content from a requested line range.

Use when:
- You need to inspect source, configuration, documentation, or other text files before reasoning about them.
- You need exact surrounding lines for a planned edit.

Prefer instead:
- Use `grep` to find text across files.
- Use `glob` to find files by pathname pattern.

Rules:
- Read relevant files before editing existing files.
- Use `offset` and `limit` for large files or when you only need a specific region.
- This tool is for text files; binary formats may be unreadable or lossy.

Returns:
- Line-numbered text beginning at the requested offset.
- Metadata includes the normalized path, applied offset, and returned line count.

If it fails:
- If the path is missing or is a directory, use `glob` or `grep` to locate the intended file.
- If access is rejected, choose another project path or ask the user for clarification.
"""
