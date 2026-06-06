"""Parser for user-authored @file mentions."""

from __future__ import annotations

from dataclasses import dataclass
import re


LINE_FRAGMENT_RE = re.compile(r"^L(?P<start>[1-9]\d*)(?:-(?P<end>[1-9]\d*))?$")


@dataclass(frozen=True)
class AtMention:
    raw: str
    path_text: str
    line_start: int | None = None
    line_end: int | None = None


def extract_at_mentions(text: str) -> tuple[AtMention, ...]:
    """Return ordered, de-duplicated @path mentions without touching disk."""

    mentions: list[AtMention] = []
    seen: set[str] = set()
    index = 0
    while index < len(text):
        if text[index] != "@":
            index += 1
            continue
        mention, next_index = _scan_mention(text, index)
        if mention is None:
            index = next_index
            continue
        key = mention.path_text.casefold().replace("\\", "/")
        if key not in seen:
            seen.add(key)
            mentions.append(mention)
        index = next_index
    return tuple(mentions)


def parse_line_fragment(raw: str) -> tuple[int | None, int | None]:
    """Parse #L10 or #L10-20; non-line fragments are intentionally ignored."""

    match = LINE_FRAGMENT_RE.match(raw)
    if match is None:
        return None, None
    start = int(match.group("start"))
    end_text = match.group("end")
    end = int(end_text) if end_text is not None else start
    if end < start:
        end = start
    return start, end


def _scan_mention(text: str, at_index: int) -> tuple[AtMention | None, int]:
    if at_index + 1 >= len(text):
        return None, at_index + 1
    if text[at_index + 1] == '"':
        return _scan_quoted_mention(text, at_index)
    return _scan_plain_mention(text, at_index)


def _scan_quoted_mention(text: str, at_index: int) -> tuple[AtMention | None, int]:
    end_quote = text.find('"', at_index + 2)
    if end_quote < 0:
        return None, at_index + 1
    raw_path = text[at_index + 2 : end_quote].strip()
    next_index = end_quote + 1
    fragment = ""
    if next_index < len(text) and text[next_index] == "#":
        fragment, next_index = _consume_fragment(text, next_index + 1)
    return _mention_from_parts(text[at_index:next_index], raw_path, fragment), next_index


def _scan_plain_mention(text: str, at_index: int) -> tuple[AtMention | None, int]:
    next_index = at_index + 1
    while next_index < len(text) and not text[next_index].isspace():
        next_index += 1
    raw = text[at_index:next_index].rstrip(".,;:!?)")
    next_index = at_index + len(raw)
    path_text, fragment = _split_fragment(raw[1:])
    return _mention_from_parts(raw, path_text.strip(), fragment), next_index


def _consume_fragment(text: str, start: int) -> tuple[str, int]:
    index = start
    while index < len(text) and not text[index].isspace():
        index += 1
    fragment = text[start:index].rstrip(".,;:!?)")
    return fragment, start + len(fragment)


def _split_fragment(value: str) -> tuple[str, str]:
    if "#" not in value:
        return value, ""
    path_text, fragment = value.split("#", 1)
    return path_text, fragment


def _mention_from_parts(
    raw: str,
    path_text: str,
    fragment: str,
) -> AtMention | None:
    if not path_text:
        return None
    line_start, line_end = parse_line_fragment(fragment) if fragment else (None, None)
    return AtMention(
        raw=raw,
        path_text=path_text,
        line_start=line_start,
        line_end=line_end,
    )
