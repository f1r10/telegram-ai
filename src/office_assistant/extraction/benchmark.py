"""Synthetic, non-confidential extraction evaluation primitives."""

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    language: str
    source: str
    expected_types: tuple[str, ...]


SYNTHETIC_CASES = (
    BenchmarkCase("az-payment", "az", "ABC MMC 5 sentyabr 2026-da 1200 AZN ödədi.", ("PAYMENT",)),
    BenchmarkCase(
        "az-customer-price",
        "az",
        "ABC MMC üçün KA45 qiyməti 125 AZN razılaşdırılıb.",
        ("CUSTOMER", "PRODUCT", "PRICE"),
    ),
    BenchmarkCase("az-general-price", "az", "KA46 ümumi qiyməti 80 AZN-dir.", ("PRODUCT", "PRICE")),
    BenchmarkCase("tr-alias", "tr", "KA-45, KA45 ürününün eski kodudur.", ("PRODUCT_ALIAS",)),
    BenchmarkCase("ru-price", "ru", "Цена товара KA47 составляет 90 USD.", ("PRODUCT", "PRICE")),
    BenchmarkCase("en-payment", "en", "XYZ LLC paid EUR 500 on 2026-09-01.", ("PAYMENT",)),
    BenchmarkCase("ambiguous-customer", "az", "ABC üçün qiymət 40 AZN.", ("PRICE",)),
    BenchmarkCase("unknown-product", "az", "Yeni model X-Pro 12 70 AZN-dir.", ("PRODUCT", "PRICE")),
    BenchmarkCase(
        "conflicting-prices", "az", "KA45 120 AZN, digər siyahıda 135 AZN.", ("PRICE", "PRICE")
    ),
    BenchmarkCase("not-payment", "en", "The project budget is 10,000 USD.", ()),
    BenchmarkCase(
        "proposal",
        "az",
        "ABC MMC üçün T-1 təklifi: KA45 2 ədəd 125 AZN; naməlum detal 1 ədəd 20 AZN.",
        ("PROPOSAL", "PROPOSAL_ITEM", "PROPOSAL_ITEM"),
    ),
)


@dataclass(frozen=True, slots=True)
class ExtractionMetrics:
    precision: float
    recall: float
    field_accuracy: float
    entity_resolution_accuracy: float
    hallucinated_count: int
    source_reference_accuracy: float


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    candidate_type: str
    fields: tuple[tuple[str, str], ...] = ()
    source_refs: tuple[str, ...] = ()
    entity_resolution: str | None = None


def score_extraction(
    expected: tuple[CandidateObservation, ...],
    actual: tuple[CandidateObservation, ...],
) -> ExtractionMetrics:
    """Score typed candidates and their evidence without treating JSON validity as quality."""

    expected_counts = Counter(item.candidate_type for item in expected)
    actual_counts = Counter(item.candidate_type for item in actual)
    true_positive = sum(
        min(count, actual_counts[candidate_type])
        for candidate_type, count in expected_counts.items()
    )

    remaining = list(actual)
    matched: list[tuple[CandidateObservation, CandidateObservation | None]] = []
    for wanted in expected:
        match_index = next(
            (
                index
                for index, observed in enumerate(remaining)
                if observed.candidate_type == wanted.candidate_type
            ),
            None,
        )
        observed = remaining.pop(match_index) if match_index is not None else None
        matched.append((wanted, observed))

    field_total = sum(len(item.fields) for item in expected)
    field_correct = sum(
        sum(dict(observed.fields).get(key) == value for key, value in wanted.fields)
        for wanted, observed in matched
        if observed is not None
    )
    resolution_pairs = [
        (wanted, observed) for wanted, observed in matched if wanted.entity_resolution is not None
    ]
    resolution_correct = sum(
        observed is not None and observed.entity_resolution == wanted.entity_resolution
        for wanted, observed in resolution_pairs
    )
    source_total = sum(len(item.source_refs) for item in expected)
    source_correct = sum(
        len(set(wanted.source_refs) & set(observed.source_refs))
        for wanted, observed in matched
        if observed is not None
    )
    return ExtractionMetrics(
        precision=true_positive / len(actual) if actual else float(not expected),
        recall=true_positive / len(expected) if expected else float(not actual),
        field_accuracy=field_correct / field_total if field_total else 1.0,
        entity_resolution_accuracy=(
            resolution_correct / len(resolution_pairs) if resolution_pairs else 1.0
        ),
        hallucinated_count=len(remaining),
        source_reference_accuracy=source_correct / source_total if source_total else 1.0,
    )
