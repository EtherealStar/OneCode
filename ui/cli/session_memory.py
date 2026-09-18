"""Backwards-compatible CLI import path for the session-memory adapter.

The implementation moved to ``application.runtime`` so the application layer
can assemble it without importing UI. Existing CLI callers keep importing from
here.
"""

from __future__ import annotations

from application.runtime import BackgroundSessionMemoryExtractor

__all__ = ["BackgroundSessionMemoryExtractor"]
