"""Per-statement epistemic state (promt.md 7, 13.4).

The three-state contract itself is declared once, next to the evidence contracts it
labels, in :class:`office_assistant.internal_evidence.models.StatementStatus`, so the
shipped service and this engine can never disagree about it:

============== =========== ==================================================
value          label       meaning
============== =========== ==================================================
``tesdiqlendi`` Təsdiqləndi an identified source *and* a locator inside it
``ehtimal``     Ehtimal     evidence exists but is partial, inferred or single-sourced
``namelum``     Naməlum     no evidence was found
============== =========== ==================================================

This module owns the *derivation*: which state a statement earns from what it can
actually point at. A hypothesis - a concept-expansion connector, an image-derived
identity, an inferred relation - is never ``confirmed``, whatever the model believes
(promt.md 5, 9, 10, 13.4).
"""

from __future__ import annotations

from collections.abc import Mapping

from office_assistant.internal_evidence.models import StatementStatus

__all__ = ["StatementStatus", "statement_status"]


def statement_status(
    *,
    evidence_id: str | None,
    locator: Mapping[str, str] | None,
    inferred: bool = False,
) -> StatementStatus:
    """Derive the state from what the statement can actually point at.

    ``inferred`` is for content the system produced rather than read: it never reaches
    ``CONFIRMED``, because no source states it.
    """

    if inferred:
        return StatementStatus.PROBABLE
    if evidence_id and locator:
        return StatementStatus.CONFIRMED
    if evidence_id or locator:
        # Something is known but cannot be pointed at: degraded, never presented as confirmed.
        return StatementStatus.PROBABLE
    return StatementStatus.UNKNOWN
