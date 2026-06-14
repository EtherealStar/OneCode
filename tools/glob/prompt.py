"""Prompt section for the glob tool."""

PROMPT = """Purpose:
Find files by pathname pattern under a directory.

Use when:
- You know a filename, extension, or path pattern and need matching files.
- You need to explore project structure without reading file contents.

Prefer instead:
- Use `grep` when matching by file contents.
- Use `read_file` after choosing a specific file to inspect.

Rules:
- Patterns are matched against paths relative to the selected search path.
- Results include files only, sorted by newest modification time first.
- Use `path` to narrow the search root and `offset` / `head_limit` to page through many matches.

Returns:
- A count plus matching paths.
- Pagination details when not all matches are shown.

If it fails:
- If the directory is missing or invalid, search from a broader known project directory or ask the user for the intended path.
"""
