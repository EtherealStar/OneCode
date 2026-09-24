from __future__ import annotations


def reasoning_preview(content: str, limit: int = 80) -> str:
    """折叠态只显示原文第一个非空片段，不调用模型做摘要。"""
    first = next((line.strip() for line in content.splitlines() if line.strip()), "思考过程")
    first = " ".join(first.split())
    return first if len(first) <= limit else first[: max(0, limit - 1)] + "…"

