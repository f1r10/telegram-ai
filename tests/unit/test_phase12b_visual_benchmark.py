import json
import sys
from pathlib import Path

import pytest

from office_assistant.scripts.benchmark_visual import (
    Prediction,
    TruthCase,
    evaluate,
    load_predictions,
    load_truth,
    main,
)


def _cases() -> list[TruthCase]:
    return [
        TruthCase("q1", "different_angle", "KA45", ()),
        TruthCase("q2", "weak_light", "KA46", ()),
        TruthCase("q3", "similar_models", None, ("KA47",)),
        TruthCase("q4", "unrelated_item", None, ()),
    ]


def _predictions() -> list[Prediction]:
    return [
        Prediction("q1", "EXACT_MATCH", ("KA45", "KA46")),
        Prediction("q2", "PROBABLE_MATCH", ("KA47", "KA46")),
        Prediction("q3", "SIMILAR", ("KA47",)),
        Prediction("q4", "NO_MATCH", ()),
    ]


def test_ranking_metrics_have_explicit_positive_and_negative_denominators() -> None:
    metrics = evaluate(_cases(), _predictions())

    assert metrics["queries"] == 4
    assert metrics["exact_queries"] == 2
    assert metrics["no_exact_queries"] == 2
    assert metrics["recall_at_1"] == 0.5
    assert metrics["recall_at_3"] == 1.0
    assert metrics["recall_at_5"] == 1.0
    assert metrics["mrr"] == 0.75
    assert metrics["exact_identity_precision"] == 1.0
    assert metrics["false_positive_rate"] == 0.0
    assert metrics["similar_recall_at_5"] == 1.0


def test_false_exact_claim_is_counted_without_declaring_similarity_to_be_identity() -> None:
    predictions = _predictions()
    predictions[-1] = Prediction("q4", "EXACT_MATCH", ("KA99",))
    metrics = evaluate(_cases(), predictions)

    assert metrics["exact_identity_precision"] == 0.5
    assert metrics["false_positive_rate"] == 0.5


def test_no_applicable_denominator_is_not_reported_as_perfect_accuracy() -> None:
    metrics = evaluate(
        [TruthCase("q1", "unrelated_item", None, ())],
        [Prediction("q1", "NO_MATCH", ())],
    )
    assert metrics["recall_at_1"] is None
    assert metrics["mrr"] is None
    assert metrics["exact_identity_precision"] is None
    assert metrics["false_positive_rate"] == 0.0
    assert metrics["similar_recall_at_5"] is None


def test_missing_or_duplicate_prediction_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="Predictions"):
        evaluate(_cases(), _predictions()[:2])
    with pytest.raises(ValueError, match="Duplicate query_id"):
        evaluate(_cases(), _predictions() + [_predictions()[0]])


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_input_canonicalization_uses_existing_product_code_rules(tmp_path: Path) -> None:
    path = tmp_path / "truth.jsonl"
    _write_jsonl(
        path,
        [
            {
                "query_id": "angle-1",
                "scenario": "different_angle",
                "exact_code": "KA-45",
                "similar_codes": ["KA 46"],
            }
        ],
    )
    truth = load_truth(path)
    assert truth == [TruthCase("angle-1", "different_angle", "KA45", ("KA46",))]


@pytest.mark.parametrize(
    "row",
    [
        {"query_id": "q1", "status": "UNKNOWN", "ranked_codes": []},
        {"query_id": "q1", "status": "EXACT_MATCH", "ranked_codes": []},
        {"query_id": "q1", "status": "NO_MATCH", "ranked_codes": ["KA45", "KA-45"]},
        {"query_id": "q1", "status": "NO_MATCH", "ranked_codes": [], "secret": "no"},
    ],
)
def test_invalid_prediction_rows_are_refused(tmp_path: Path, row: dict[str, object]) -> None:
    path = tmp_path / "predictions.jsonl"
    _write_jsonl(path, [row])
    with pytest.raises(ValueError):
        load_predictions(path)


def test_duplicate_canonical_truth_codes_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "truth.jsonl"
    _write_jsonl(
        path,
        [
            {
                "query_id": "q1",
                "scenario": "crop",
                "exact_code": None,
                "similar_codes": ["KA45", "KA-45"],
            }
        ],
    )
    with pytest.raises(ValueError, match="duplicate canonical"):
        load_truth(path)


def test_cli_reports_only_metrics_and_hashes_not_product_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    truth_path = tmp_path / "truth.jsonl"
    predictions_path = tmp_path / "predictions.jsonl"
    _write_jsonl(
        truth_path,
        [
            {
                "query_id": "hidden-q1",
                "scenario": "phone_photo",
                "exact_code": "PRIVATE-KA45",
                "similar_codes": [],
            }
        ],
    )
    _write_jsonl(
        predictions_path,
        [{"query_id": "hidden-q1", "status": "EXACT_MATCH", "ranked_codes": ["PRIVATE-KA45"]}],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["benchmark_visual", "--truth", str(truth_path), "--predictions", str(predictions_path)],
    )
    main()
    printed = capsys.readouterr().out
    result = json.loads(printed)
    assert result["metrics"]["exact_identity_precision"] == 1.0
    assert len(result["ground_truth_sha256"]) == 64
    assert "PRIVATE-KA45" not in printed
    assert "hidden-q1" not in printed
