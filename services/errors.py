"""Provider-neutral error types and classification helpers.

This module is intentionally low level.  It only depends on the Python
standard library so core, services, tools, and infrastructure can classify
errors without creating import cycles.
"""

from __future__ import annotations

import asyncio
import errno
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    ABORT = "abort"
    CONFIGURATION = "configuration"
    PROVIDER = "provider"
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    CONTEXT_LIMIT = "context_limit"
    INVALID_RESPONSE = "invalid_response"
    FILESYSTEM = "filesystem"
    SHELL = "shell"
    MCP = "mcp"
    TOOL = "tool"
    PERMISSION = "permission"
    INTERNAL = "internal"


@dataclass(frozen=True)
class ErrorDetails:
    category: ErrorCategory
    error_type: str
    message: str
    safe_message: str
    retryable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class OneCodeError(Exception):
    """Base class for classified runtime errors."""

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory | str,
        retryable: bool = False,
        safe_message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.category = ErrorCategory(category)
        self.retryable = retryable
        self.safe_message = safe_message or message
        self.metadata = dict(metadata or {})


class AbortError(OneCodeError):
    def __init__(self, message: str = "Operation aborted.") -> None:
        super().__init__(message, category=ErrorCategory.ABORT, safe_message=message)


class ConfigParseError(OneCodeError):
    def __init__(
        self,
        message: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        safe_message: str = "Configuration could not be parsed.",
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.CONFIGURATION,
            safe_message=safe_message,
            metadata=metadata,
        )


class ShellError(OneCodeError):
    def __init__(
        self,
        message: str = "Shell command failed.",
        *,
        code: int | None = None,
        interrupted: bool = False,
        safe_message: str = "Shell command failed.",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        merged = dict(metadata or {})
        if code is not None:
            merged["code"] = code
        merged["interrupted"] = interrupted
        super().__init__(
            message,
            category=ErrorCategory.SHELL,
            safe_message=safe_message,
            metadata=merged,
        )
        self.code = code
        self.interrupted = interrupted


class McpOperationError(OneCodeError):
    def __init__(
        self,
        message: str,
        *,
        safe_message: str = "MCP operation failed.",
        metadata: Mapping[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.MCP,
            retryable=retryable,
            safe_message=safe_message,
            metadata=metadata,
        )


class ToolRuntimeError(OneCodeError):
    def __init__(
        self,
        message: str,
        *,
        safe_message: str = "Tool runtime failed.",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.TOOL,
            safe_message=safe_message,
            metadata=metadata,
        )


class RetryExhaustedError(OneCodeError):
    def __init__(
        self,
        message: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            category=ErrorCategory.PROVIDER,
            safe_message="Provider retry attempts were exhausted.",
            metadata=metadata,
        )


def to_error(value: object) -> BaseException:
    if isinstance(value, BaseException):
        return value
    return Exception(str(value))


def error_message(value: object) -> str:
    if isinstance(value, BaseException):
        return str(value)
    return str(value)


def short_error_stack(value: object, max_frames: int = 5) -> str:
    error = to_error(value)
    if error.__traceback__ is None:
        return f"{type(error).__name__}: {error}"
    frames = traceback.extract_tb(error.__traceback__)
    selected = frames[-max(0, max_frames):]
    formatted = "".join(traceback.format_list(selected))
    return f"{type(error).__name__}: {error}\n{formatted}".rstrip()


def errno_code(value: object) -> str | None:
    code = getattr(value, "code", None)
    if isinstance(code, str):
        return code
    if isinstance(value, OSError) and value.errno is not None:
        return errno.errorcode.get(value.errno)
    return None


def errno_path(value: object) -> str | None:
    path = getattr(value, "filename", None) or getattr(value, "path", None)
    if isinstance(path, (str, bytes)):
        return path.decode() if isinstance(path, bytes) else path
    return None


def is_fs_inaccessible(value: object) -> bool:
    if isinstance(value, (FileNotFoundError, PermissionError, NotADirectoryError)):
        return True
    if not isinstance(value, OSError):
        return False
    return value.errno in {
        errno.ENOENT,
        errno.EACCES,
        errno.EPERM,
        errno.ENOTDIR,
        errno.ELOOP,
    }


def is_abort_error(value: object) -> bool:
    if isinstance(value, (AbortError, asyncio.CancelledError)):
        return True
    return isinstance(value, BaseException) and type(value).__name__ == "AbortError"


def onecode_error_details(value: object) -> ErrorDetails:
    error = to_error(value)
    if isinstance(error, OneCodeError):
        return ErrorDetails(
            category=error.category,
            error_type=_error_type(error),
            message=str(error),
            safe_message=error.safe_message,
            retryable=error.retryable,
            metadata=dict(error.metadata),
        )
    if is_abort_error(error):
        return _details(error, ErrorCategory.ABORT)
    if is_fs_inaccessible(error):
        metadata: dict[str, Any] = {}
        code = errno_code(error)
        path = errno_path(error)
        if code is not None:
            metadata["errno"] = code
        if path is not None:
            metadata["path"] = path
        return _details(error, ErrorCategory.FILESYSTEM, metadata=metadata)

    category = _category_from_error_shape(error)
    retryable = bool(getattr(error, "retryable", False))
    metadata = _metadata_from_error_shape(error)
    return _details(error, category, retryable=retryable, metadata=metadata)


def _details(
    error: BaseException,
    category: ErrorCategory,
    *,
    retryable: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> ErrorDetails:
    return ErrorDetails(
        category=category,
        error_type=_error_type(error),
        message=str(error),
        safe_message=str(error),
        retryable=retryable,
        metadata=dict(metadata or {}),
    )


def _error_type(error: BaseException) -> str:
    explicit = getattr(error, "error_type", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    return type(error).__name__


def _category_from_error_shape(error: BaseException) -> ErrorCategory:
    error_type = _error_type(error)
    mapped = _provider_error_category(error_type)
    if mapped is not None:
        return mapped
    name = type(error).__name__.lower()
    if "mcp" in name:
        return ErrorCategory.MCP
    if "shell" in name or "command" in name:
        return ErrorCategory.SHELL
    if "tool" in name:
        return ErrorCategory.TOOL
    if "permission" in name or "forbidden" in name or "denied" in name:
        return ErrorCategory.PERMISSION
    return ErrorCategory.INTERNAL


def _provider_error_category(error_type: str) -> ErrorCategory | None:
    return {
        "rate_limit_error": ErrorCategory.RATE_LIMIT,
        "context_limit_exceeded": ErrorCategory.CONTEXT_LIMIT,
        "network_error": ErrorCategory.NETWORK,
        "timeout_error": ErrorCategory.NETWORK,
        "configuration_error": ErrorCategory.CONFIGURATION,
        "authentication_error": ErrorCategory.PROVIDER,
        "server_error": ErrorCategory.PROVIDER,
        "invalid_response": ErrorCategory.INVALID_RESPONSE,
        "invalid_tool_arguments": ErrorCategory.INVALID_RESPONSE,
    }.get(error_type)


def _metadata_from_error_shape(error: BaseException) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("provider_id", "status_code", "retry_after_seconds"):
        value = getattr(error, key, None)
        if value is not None:
            metadata[key] = value
    return metadata
