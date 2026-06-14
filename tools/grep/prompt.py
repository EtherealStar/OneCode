"""Prompt section for the grep tool."""

PROMPT = """Purpose:
Search file contents with ripgrep-compatible regular expressions.

Use when:
- You need to find files containing text, symbols, imports, or error messages.
- You need matching lines, per-file counts, or a list of matching files.

Prefer instead:
- Use `glob` when matching only file paths.
- Use `read_file` after locating a specific file or line range.

Rules:
- The default output mode is `files_with_matches`.
- Use `output_mode="content"` when exact matching lines and line numbers are needed.
- Use `output_mode="count"` when comparing match counts by file.
- Narrow broad searches with `path`, `glob`, or `type`; use `offset` and `head_limit` for large result sets.
- Context options are only valid with `output_mode="content"`.

Returns:
- Matching files, counts, or content lines depending on `output_mode`.
- Pagination details when not all matches are shown.

If it fails:
- If no matches are found, adjust case sensitivity, regex syntax, path, file type, or glob filters.
- If ripgrep is unavailable, use `glob` and `read_file` for narrower manual inspection.
"""
