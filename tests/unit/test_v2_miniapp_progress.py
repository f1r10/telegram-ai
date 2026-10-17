"""Progress must reflect committed parse/index facts, never presumed readiness."""

import pytest

from office_assistant.miniapp.progress import summarize_progress


@pytest.mark.parametrize(
    ("ingest", "parse_states", "index", "chunks", "expected", "terminal"),
    [
        ("PENDING", [], None, 0, "queued", False),
        ("RUNNING", [], None, 0, "reading", False),
        ("FAILED", [], None, 0, "failed", True),
        ("PARTIAL", ["SUCCEEDED"], "SUCCEEDED", 12, "review", True),
        ("SUCCEEDED", [], None, 0, "reading", False),
        ("SUCCEEDED", ["SUCCEEDED"], None, 0, "indexing", False),
        ("SUCCEEDED", ["SUCCEEDED"], "PENDING", 0, "indexing", False),
        ("SUCCEEDED", ["SUCCEEDED"], "RUNNING", 0, "indexing", False),
        ("SUCCEEDED", ["SUCCEEDED"], "SUCCEEDED", 0, "review", True),
        ("SUCCEEDED", ["SUCCEEDED"], "SUCCEEDED", 12, "ready", True),
        ("SUCCEEDED", ["DEFERRED"], "SUCCEEDED", 12, "review", True),
        ("SUCCEEDED", ["FAILED"], "SUCCEEDED", 12, "review", True),
        ("SUCCEEDED", ["RUNNING"], "SUCCEEDED", 12, "reading", False),
        ("SUCCEEDED", ["SUCCEEDED"], "PARTIAL", 2, "review", True),
        ("SUCCEEDED", ["SUCCEEDED"], "FAILED", 0, "failed", True),
    ],
)
def test_document_progress_from_durable_states(
    ingest: str,
    parse_states: list[str],
    index: str | None,
    chunks: int,
    expected: str,
    terminal: bool,
) -> None:
    stage, notice, finished = summarize_progress(
        ingest=ingest,
        parse_states=parse_states,
        index=index,
        indexed_chunks=chunks,
    )
    assert stage == expected
    assert finished is terminal
    assert notice
