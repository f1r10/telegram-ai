"""Per-parse availability boundary (Phase 2 M9, audit gap G11).

**This is an availability boundary, not a security sandbox.** It stops one hostile or pathological
document from occupying the ingestion worker indefinitely: the parse runs in a child process, the
parent kills it when the wall clock expires, and — where the operating system exposes
``RLIMIT_AS`` — the child's address space is capped. It does not confine filesystem or network
access, it does not make untrusted parsers safe, and it must never be described as a sandbox.

The boundary is **off by default**: a deployment that has not opted in runs the pre-M9 parse path
in-process with no extra process, and no configured ceiling is invented for it.

ASSUMPTION: the child result travels back pickled, so a result larger than
``MAX_RESULT_BYTES`` aborts with ``PARSE_RESULT_LIMIT`` instead of filling the parent's memory.
See docs/OPEN-QUESTIONS.md Q62.
"""

# The ``resource`` module exists only on POSIX, so a Windows type check cannot see its attributes.
# pyright: reportAttributeAccessIssue=false, reportUnknownMemberType=false
from __future__ import annotations

import importlib.util
import multiprocessing
import pickle
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from office_assistant.core.settings import Settings

#: Sanitized abort codes. A caller records the code; document text never appears in it.
PARSE_TIME_LIMIT = "PARSE_TIME_LIMIT"
PARSE_MEMORY_LIMIT = "PARSE_MEMORY_LIMIT"
PARSE_RESULT_LIMIT = "PARSE_RESULT_LIMIT"
PARSE_PROCESS_FAILED = "PARSE_PROCESS_FAILED"

#: A parse result larger than this is not carried back through the parent (bytes).
MAX_RESULT_BYTES = 256 * 1024 * 1024
#: Smallest ceiling worth configuring: below this the interpreter itself cannot start.
MIN_MEMORY_LIMIT_MB = 64


class ParseAborted(Exception):
    """A parse stopped at the availability boundary. Carries only a sanitized code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ParseBudget:
    """The configured ceilings for one parse. At least one must be set.

    ``wall_clock_seconds=None`` means "no wall-clock ceiling": the child is then waited on until it
    finishes, which is only sensible when the memory ceiling is set. Both unset is rejected, because
    a budget that enforces nothing would be a promise the code does not keep.
    """

    wall_clock_seconds: float | None = None
    memory_limit_mb: int | None = None

    def __post_init__(self) -> None:
        if self.wall_clock_seconds is None and self.memory_limit_mb is None:
            raise ValueError("A parse budget needs at least one ceiling")
        if self.wall_clock_seconds is not None and self.wall_clock_seconds <= 0:
            raise ValueError("The parse wall-clock ceiling must be greater than zero")
        if self.memory_limit_mb is not None and self.memory_limit_mb < MIN_MEMORY_LIMIT_MB:
            raise ValueError(f"The parse memory ceiling must be at least {MIN_MEMORY_LIMIT_MB} MB")


def parse_budget_from_settings(settings: Settings) -> ParseBudget | None:
    """The configured boundary, or ``None`` when document intelligence is off or nothing is set.

    The gate is deliberate: Phase 2's enforcement path is only active with
    ``gi_document_intelligence_enabled``, so an opted-out deployment keeps today's in-process parse.
    """

    if not settings.gi_document_intelligence_enabled:
        return None
    if settings.parse_wall_clock_seconds is None and settings.parse_memory_limit_mb is None:
        return None
    return ParseBudget(
        wall_clock_seconds=settings.parse_wall_clock_seconds,
        memory_limit_mb=settings.parse_memory_limit_mb,
    )


def memory_limit_supported() -> bool:
    """True when this platform can cap the child's address space (POSIX ``RLIMIT_AS``)."""

    return importlib.util.find_spec("resource") is not None


def _apply_memory_limit(limit_mb: int | None) -> None:
    """Cap the child's address space where the OS supports it; do nothing elsewhere."""

    if limit_mb is None:
        return
    try:
        import resource
    except ImportError:  # Windows has no resource module; the wall clock still applies.
        return
    limit = limit_mb * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (OSError, ValueError):
        # A refused limit must not turn every parse into a failure; the process boundary and the
        # wall clock remain, and memory_limit_supported() records the limitation for the operator.
        return


def _child(connection: Any, operation: Callable[[], Any], budget: ParseBudget) -> None:
    """Run the operation in the child and send back a sanitized outcome."""

    try:
        _apply_memory_limit(budget.memory_limit_mb)
        result = operation()
        payload = pickle.dumps(result)
        if len(payload) > MAX_RESULT_BYTES:
            connection.send(("error", PARSE_RESULT_LIMIT))
            return
        connection.send(("ok", payload))
    except MemoryError:
        connection.send(("error", PARSE_MEMORY_LIMIT))
    except BaseException as error:  # noqa: BLE001 - boundary reports a code, never a traceback
        connection.send(("error", type(error).__name__))
    finally:
        connection.close()


def run_bounded[T](operation: Callable[[], T], budget: ParseBudget) -> T:
    """Run ``operation`` in a killable child process and enforce the budget.

    On expiry the child is killed and :class:`ParseAborted` carries ``PARSE_TIME_LIMIT``; a child
    that exhausts its address space reports ``PARSE_MEMORY_LIMIT``; a child that dies without
    reporting (segfault, OOM killer) reports ``PARSE_PROCESS_FAILED``.
    """

    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_child, args=(child, operation, budget), daemon=True)
    process.start()
    child.close()
    try:
        if budget.wall_clock_seconds is None:
            process.join()
            ready = True
        else:
            ready = parent.poll(budget.wall_clock_seconds)
        if not ready:
            process.kill()
            process.join(timeout=5)
            raise ParseAborted(PARSE_TIME_LIMIT)
        try:
            kind, payload = parent.recv()
        except EOFError:
            raise ParseAborted(PARSE_PROCESS_FAILED) from None
    finally:
        parent.close()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
    if kind != "ok":
        raise ParseAborted(str(payload))
    return pickle.loads(payload)
