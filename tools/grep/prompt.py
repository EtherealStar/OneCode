"""Prompt section for the grep tool."""

PROMPT = """grep: Search file contents with a regular expression inside the sandbox.

Use grep when you need to find files containing text or inspect matching lines. The
default output_mode is files_with_matches; use content when exact matching lines and
line numbers are needed, and count when per-file match counts are needed. Results are
read-only, sandbox-filtered, and may be paginated with offset and head_limit."""
