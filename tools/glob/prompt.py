"""Prompt section for the glob tool."""

PROMPT = """glob: Find files by pathname pattern inside the sandbox.

Use glob when you need a list of files matching a pattern such as **/*.py or docs/*.md.
The pattern is matched against paths relative to the selected search path. Results are
read-only, sandbox-filtered, sorted by newest modification time first, and may be
paginated with offset and head_limit."""
