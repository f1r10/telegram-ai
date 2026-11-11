"""Bounded multi-topic decomposition (architecture §3, defect F4).

A single question may carry several topics that need different source groups. This splits on
explicit separators (comma, semicolon, question mark, and the Azerbaijani/Turkish conjunction
"və"/"ve") into at most a handful of parts, each becoming its own sub-question. It is deliberately
conservative: a question with no separator stays one part, so single-topic routing is unchanged.
"""

from __future__ import annotations

import re

_SEPARATOR = re.compile(r"[,;?]|\bvə\b|\bve\b", re.IGNORECASE)
_MAX_PARTS = 8


def decompose(question: str) -> list[str]:
    """Split a question into non-empty topic parts; never returns an empty list."""

    parts = [part.strip() for part in _SEPARATOR.split(question)]
    parts = [part for part in parts if part][:_MAX_PARTS]
    return parts or [question.strip()]
