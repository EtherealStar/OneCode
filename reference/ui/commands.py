from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz.fuzz import WRatio


class CommandName(StrEnum):
    MODEL = "/model"
    CONNECT = "/connect"
    SESSION = "/session"
    STATUS = "/status"
    CLEAR = "/clear"
    QUIT = "/quit"


@dataclass(frozen=True, slots=True)
class CommandSuggestion:
    command: CommandName
    description: str


COMMAND_SUGGESTIONS = (
    CommandSuggestion(CommandName.MODEL, "选择模型"),
    CommandSuggestion(CommandName.CONNECT, "配置模型连接"),
    CommandSuggestion(CommandName.SESSION, "打开历史会话"),
    CommandSuggestion(CommandName.STATUS, "查看当前状态"),
    CommandSuggestion(CommandName.CLEAR, "清空当前会话"),
    CommandSuggestion(CommandName.QUIT, "退出 MiniAgent"),
)


def parse_command(text: str) -> CommandName | None:
    """只把首 token 的完整匹配识别为命令。"""
    tokens = text.strip().split(maxsplit=1)
    if not tokens:
        return None
    try:
        return CommandName(tokens[0])
    except ValueError:
        return None


def complete_command(text: str) -> str | None:
    value = text.strip()
    matches = [command.value for command in CommandName if command.value.startswith(value)]
    return matches[0] if value.startswith("/") and len(matches) == 1 else None


def has_slash_query(text: str) -> bool:
    """首个非空白 token 以斜杠开头时，Slash Completion Mode 边界仍有效。"""

    return text.lstrip().startswith("/")


def suggest_commands(text: str) -> tuple[CommandSuggestion, ...]:
    """按匹配等级和符合度确定性排列 Composer 的公开命令候选。"""
    value = text.lstrip()
    if not value.startswith("/"):
        return ()
    token = value.split(maxsplit=1)[0][1:]
    if not token:
        return COMMAND_SUGGESTIONS

    ranked: list[tuple[int, float, int, CommandSuggestion]] = []
    for declaration_index, item in enumerate(COMMAND_SUGGESTIONS):
        candidate = item.command.value[1:]
        score = float(WRatio(token, candidate))
        if score < 60:
            continue
        match_level = 0 if token == candidate else 1 if candidate.startswith(token) else 2
        ranked.append((match_level, -score, declaration_index, item))
    ranked.sort(key=lambda match: match[:3])
    return tuple(match[3] for match in ranked)

