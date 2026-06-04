"""Tool schema projection helpers."""

from __future__ import annotations

from typing import Any

from services.tools.types import ToolDescriptor


def descriptor_to_openai_tool_schema(
    descriptor: ToolDescriptor,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": descriptor.name,
            "description": descriptor.description,
            "parameters": descriptor.input_schema,
        },
    }
