from services.attachments.parser import extract_at_mentions


def test_extracts_paths_and_line_ranges_in_order() -> None:
    mentions = extract_at_mentions(
        'read @architecture.md#L1-5 and @"docs/design docs/core beliefs.md"#L10'
    )

    assert [mention.path_text for mention in mentions] == [
        "architecture.md",
        "docs/design docs/core beliefs.md",
    ]
    assert mentions[0].line_start == 1
    assert mentions[0].line_end == 5
    assert mentions[1].line_start == 10
    assert mentions[1].line_end == 10


def test_strips_non_line_fragments() -> None:
    mentions = extract_at_mentions("summarize @README.md#intro")

    assert len(mentions) == 1
    assert mentions[0].path_text == "README.md"
    assert mentions[0].line_start is None
    assert mentions[0].line_end is None


def test_dedupes_first_occurrence() -> None:
    mentions = extract_at_mentions("see @a.py @b.py @a.py#L2")

    assert [mention.path_text for mention in mentions] == ["a.py", "b.py"]
