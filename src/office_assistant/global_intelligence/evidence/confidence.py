"""The epistemic contract: per-statement confidence (promt.md §4.1, architecture §4).

Modelled once here and enforced in code, not by prompt alone. A statement may only be
CONFIRMED when it carries an identified source *and* a non-empty locator; image-derived text
is untrusted and can never rise above PROBABLE. "Not found" stays UNKNOWN, never "does not
exist".
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from office_assistant.internal_evidence.models import StrictModel


class StatementStatus(StrEnum):
    CONFIRMED = "tesdiqlendi"  # Təsdiqlənib — identified source + locator
    PROBABLE = "ehtimal"  # Ehtimal — reasoned or weakly identified, basis stated
    UNKNOWN = "namelum"  # Naməlum — no evidence found


class SourceClass(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    IMAGE_UNTRUSTED = "image_untrusted"


class Statement(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    status: StatementStatus = StatementStatus.UNKNOWN
    evidence_id: str | None = None
    locator: dict[str, object] | None = None
    source_class: SourceClass = SourceClass.INTERNAL
    basis: str = Field(default="", max_length=1000)


def effective_status(statement: Statement) -> StatementStatus:
    """Downgrade any claim the evidence cannot support (architecture §4 rules 1 and 3).

    CONFIRMED without both an evidence id and a non-empty locator is rejected to UNKNOWN.
    An untrusted image source can be at most PROBABLE even when it carries a locator.
    """

    status = statement.status
    if status is StatementStatus.CONFIRMED and not (statement.evidence_id and statement.locator):
        status = StatementStatus.UNKNOWN
    if (
        status is StatementStatus.CONFIRMED
        and statement.source_class is SourceClass.IMAGE_UNTRUSTED
    ):
        status = StatementStatus.PROBABLE
    return status
