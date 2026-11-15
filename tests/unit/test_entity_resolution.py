"""Phase 3: a mention resolves only on exact evidence, and only inside what the actor may see.

The resolver is pure, so the strength order, the ambiguity rule and the "similarity never merges"
rule are all asserted without a database. The access filter is asserted on the shared policy
function it reuses, and the flag is asserted to read nothing when it is off.
"""

import asyncio
from typing import Any, cast

from office_assistant.core.settings import Settings
from office_assistant.global_intelligence.entities import (
    CandidateReason,
    EntityAccess,
    EntityRecord,
    EntityResolutionService,
    MatchStrength,
    ResolutionStatus,
    allowed_entity_types,
    entity_resolution_service,
    entity_scope,
    resolve,
)
from office_assistant.infrastructure.database.models import EntityType, User, UserRole, UserStatus


def _product(
    entity_id: str,
    name: str,
    *,
    normalized: str | None = None,
    aliases: tuple[str, ...] = (),
    identifiers: tuple[str, ...] = (),
) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        entity_type=EntityType.PRODUCT,
        canonical_name=name,
        normalized_name=name.upper() if normalized is None else normalized,
        aliases=aliases,
        identifiers=identifiers,
    )


def _customer(
    entity_id: str,
    name: str,
    *,
    normalized: str,
    aliases: tuple[str, ...] = (),
) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        entity_type=EntityType.CUSTOMER,
        canonical_name=name,
        normalized_name=normalized,
        aliases=aliases,
    )


def _user(*, role: UserRole = UserRole.USER, status: UserStatus = UserStatus.ACTIVE) -> User:
    return User(id=1, telegram_user_id=555, role=role, status=status)


def test_an_exact_identifier_outranks_an_exact_name() -> None:
    by_name = _product("name", "KA45", normalized="KA45")
    by_code = _product("code", "Filtrlər", normalized="FILTRLƏR", identifiers=("KA45",))

    outcome = resolve("KA45", identifier="ka-45", records=[by_name, by_code])

    assert outcome.status is ResolutionStatus.RESOLVED
    assert outcome.matched_by is MatchStrength.IDENTIFIER
    assert outcome.record is not None and outcome.record.entity_id == "code"


def test_an_exact_canonical_name_outranks_an_alias() -> None:
    canonical = _customer("canonical", "Əli MMC", normalized="əli mmc")
    aliased = _customer("aliased", "Başqa MMC", normalized="başqa mmc", aliases=("əli mmc",))

    outcome = resolve("Əli MMC", records=[aliased, canonical])

    assert outcome.status is ResolutionStatus.RESOLVED
    assert outcome.matched_by is MatchStrength.CANONICAL_NAME
    assert outcome.record is not None and outcome.record.entity_id == "canonical"


def test_an_accepted_alias_resolves_the_entity() -> None:
    record = _product("p1", "Ürvant KA45", normalized="ÜRVANTKA45", aliases=("ÜRVANTKA45X",))

    outcome = resolve("ürvant ka45 x", records=[record])

    assert outcome.status is ResolutionStatus.RESOLVED
    assert outcome.matched_by is MatchStrength.ALIAS
    assert outcome.record is not None and outcome.record.entity_id == "p1"


def test_two_records_with_the_same_name_are_ambiguous_and_nothing_is_picked() -> None:
    first = _product("a", "KA45", normalized="KA45")
    second = _product("b", "KA45", normalized="KA45")

    outcome = resolve("KA45", records=[first, second])

    assert outcome.status is ResolutionStatus.AMBIGUOUS
    assert outcome.record is None, "a tie is never resolved by picking one"
    assert outcome.matched_by is MatchStrength.CANONICAL_NAME
    assert [candidate.entity_id for candidate in outcome.candidates] == ["a", "b"]
    assert {candidate.reason for candidate in outcome.candidates} == {
        CandidateReason.AMBIGUOUS_EXACT
    }
    assert {candidate.score for candidate in outcome.candidates} == {1.0}


def test_similarity_only_proposes_candidates_and_never_merges() -> None:
    record = _product("p1", "KA45", normalized="KA45")

    outcome = resolve("KA450", records=[record])

    assert outcome.status is ResolutionStatus.AMBIGUOUS
    assert outcome.record is None, "a high similarity is evidence, not an identity"
    assert outcome.matched_by is None
    assert len(outcome.candidates) == 1
    candidate = outcome.candidates[0]
    assert candidate.entity_id == "p1"
    assert candidate.reason is CandidateReason.NAME_SIMILARITY
    assert 0.0 < candidate.score < 1.0


def test_a_mention_below_the_candidate_bound_proposes_nothing() -> None:
    record = _product("p1", "KA45", normalized="KA45")

    outcome = resolve("QX99", records=[record])

    assert outcome.status is ResolutionStatus.UNRESOLVED
    assert outcome.candidates == ()
    assert outcome.record is None


def test_a_mention_that_normalizes_to_nothing_is_unresolved_not_an_error() -> None:
    assert resolve("---", records=[_product("p1", "KA45", normalized="KA45")]).status is (
        ResolutionStatus.UNRESOLVED
    )
    assert resolve("   ", records=[]).status is ResolutionStatus.UNRESOLVED


def test_azerbaijani_letters_match_across_case() -> None:
    record = _customer("c1", "Şəkər MMC", normalized="şəkər mmc")

    outcome = resolve("ŞƏKƏR MMC", records=[record])

    assert outcome.status is ResolutionStatus.RESOLVED
    assert outcome.record is not None and outcome.record.entity_id == "c1"


def test_only_the_configured_number_of_candidates_is_offered() -> None:
    records = [
        _product(f"p{index}", f"KA45{index}", normalized=f"KA45{index}") for index in range(5)
    ]

    outcome = resolve("KA45", records=records, max_candidates=2)

    assert outcome.status is ResolutionStatus.AMBIGUOUS
    assert len(outcome.candidates) == 2


def test_access_follows_the_shared_feature_policy() -> None:
    product_only = allowed_entity_types(_user(), {"product_search": True, "sales_reports": False})
    neither = allowed_entity_types(_user(), {"product_search": False, "sales_reports": False})
    admin = allowed_entity_types(_user(role=UserRole.ADMIN), {})

    assert product_only == frozenset({EntityType.PRODUCT})
    assert neither == frozenset()
    assert admin == frozenset(EntityType)


def test_a_user_with_no_stored_grants_gets_the_documented_baseline() -> None:
    assert allowed_entity_types(_user(), {}) == frozenset(EntityType)


def test_an_inactive_user_sees_no_entity_type() -> None:
    assert allowed_entity_types(_user(status=UserStatus.DISABLED), {}) == frozenset()
    assert allowed_entity_types(None, {}) == frozenset()


def test_the_scope_clause_names_only_the_visible_types() -> None:
    visible = EntityAccess(user_id=1, entity_types=frozenset({EntityType.PRODUCT}), is_admin=False)
    hidden = EntityAccess(user_id=1, entity_types=frozenset(), is_admin=False)

    visible_sql = str(entity_scope(visible).compile(compile_kwargs={"literal_binds": True}))
    hidden_sql = str(entity_scope(hidden).compile(compile_kwargs={"literal_binds": True}))

    assert "'product'" in visible_sql
    assert "'customer'" not in visible_sql, "a hidden type is filtered out of the query itself"
    assert "customer" not in hidden_sql and "product" not in hidden_sql


class _ExplodingFactory:
    """A session factory that fails if the flag-off path touches the database at all."""

    def begin(self) -> Any:
        raise AssertionError("the disabled path must not open a session")


def test_the_flag_off_path_reads_nothing_and_says_so() -> None:
    service = EntityResolutionService(cast(Any, _ExplodingFactory()), enabled=False)

    outcome = asyncio.run(service.resolve(1, "KA45"))

    assert outcome.status is ResolutionStatus.DISABLED
    assert outcome.record is None
    assert outcome.candidates == ()


def test_the_service_reads_its_default_from_the_flag() -> None:
    default = entity_resolution_service(cast(Any, _ExplodingFactory()), Settings())
    assert default.enabled is False, "gi_entity_resolution_enabled defaults to off"

    enabled = entity_resolution_service(
        cast(Any, _ExplodingFactory()), Settings(gi_entity_resolution_enabled=True)
    )
    assert enabled.enabled is True
