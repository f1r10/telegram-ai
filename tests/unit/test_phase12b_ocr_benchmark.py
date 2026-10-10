import json
import sys
from pathlib import Path

import pytest

from office_assistant.scripts.benchmark_ocr import (
    OcrPrediction,
    OcrTruth,
    evaluate_ocr,
    load_ocr_predictions,
    load_ocr_truth,
    main,
)


def _truth() -> list[OcrTruth]:
    return [
        OcrTruth("q1", "phone_photo", ("KA45",)),
        OcrTruth("q2", "phone_photo", ("KA46",)),
        OcrTruth("q3", "unrelated_item", ()),
        OcrTruth("q4", "unrelated_item", ()),
    ]


def _predictions() -> list[OcrPrediction]:
    return [
        OcrPrediction("q1", ("KA45", "KA47")),
        OcrPrediction("q2", ("KA46",)),
        OcrPrediction("q3", ("KA99",)),
        OcrPrediction("q4", ()),
    ]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_metrics_are_micro_averaged_and_report_negative_cases() -> None:
    report = evaluate_ocr(_truth(), _predictions())
    overall = report["overall"]
    assert isinstance(overall, dict)
    assert overall["queries"] == 4
    assert overall["correct_code_count"] == 2
    assert overall["visible_code_count"] == 2
    assert overall["extracted_code_count"] == 4
    assert overall["code_precision"] == 0.5
    assert overall["code_recall"] == 1.0
    assert overall["code_f1"] == 0.6667
    assert overall["query_exact_set_accuracy"] == 0.5
    assert overall["image_code_hit_rate"] == 1.0
    assert overall["false_alarm_image_rate"] == 0.5


def test_scenario_metrics_use_correct_case_denominators() -> None:
    by_scenario = evaluate_ocr(_truth(), _predictions())["by_scenario"]
    assert isinstance(by_scenario, dict)
    phone = by_scenario["phone_photo"]
    unrelated = by_scenario["unrelated_item"]
    assert phone["queries"] == 2
    assert phone["code_recall"] == 1.0
    assert phone["false_alarm_image_rate"] is None
    assert unrelated["images_without_visible_codes"] == 2
    assert unrelated["false_alarm_image_rate"] == 0.5
    assert unrelated["code_recall"] is None


def test_no_applicable_denominator_is_null() -> None:
    overall = evaluate_ocr(
        [OcrTruth("negative", "unrelated_item", ())],
        [OcrPrediction("negative", ())],
    )["overall"]
    assert isinstance(overall, dict)
    assert overall["code_precision"] is None
    assert overall["code_recall"] is None
    assert overall["code_f1"] is None
    assert overall["false_alarm_image_rate"] == 0.0
    assert overall["query_exact_set_accuracy"] == 1.0


def test_similar_looking_code_is_not_an_exact_ocr_hit() -> None:
    overall = evaluate_ocr(
        [OcrTruth("q", "similar_models", ("KA45",))],
        [OcrPrediction("q", ("KA4S",))],
    )["overall"]
    assert isinstance(overall, dict)
    assert overall["code_precision"] == 0.0
    assert overall["code_recall"] == 0.0


def test_mismatched_or_duplicate_query_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="all and only"):
        evaluate_ocr(_truth(), _predictions()[:-1])
    with pytest.raises(ValueError, match="Duplicate query_id"):
        evaluate_ocr(_truth(), _predictions() + [_predictions()[0]])


def test_canonicalization_uses_strict_approved_product_code_rules(tmp_path: Path) -> None:
    truth_path = tmp_path / "truth.jsonl"
    pred_path = tmp_path / "predictions.jsonl"
    _write_jsonl(
        truth_path,
        [{"query_id": "q", "scenario": "crop", "visible_codes": ["KA-45"]}],
    )
    _write_jsonl(
        pred_path,
        [{"query_id": "q", "extracted_codes": ["ka 45"]}],
    )
    assert load_ocr_truth(truth_path)[0].visible_codes == ("KA45",)
    assert load_ocr_predictions(pred_path)[0].extracted_codes == ("KA45",)


@pytest.mark.parametrize(
    "bad_record",
    [
        {"query_id": "q", "scenario": "unknown_company_name", "visible_codes": []},
        {"query_id": "q", "scenario": "crop", "visible_codes": ["KA45", "KA-45"]},
        {"query_id": "q", "scenario": "crop", "visible_codes": ["---"]},
        {"query_id": "q", "scenario": "crop", "visible_codes": "KA45"},
        {"query_id": "q", "scenario": "crop", "visible_codes": [], "private": "secret"},
    ],
)
def test_invalid_truth_records_are_refused(tmp_path: Path, bad_record: dict[str, object]) -> None:
    path = tmp_path / "truth.jsonl"
    _write_jsonl(path, [bad_record])
    with pytest.raises(ValueError):
        load_ocr_truth(path)


@pytest.mark.parametrize(
    "bad_record",
    [
        {"query_id": "q", "extracted_codes": ["KA45", "KA-45"]},
        {"query_id": "q", "extracted_codes": [25]},
        {"query_id": "q", "extracted_codes": None},
        {"query_id": "q", "extracted_codes": [], "confidence": 1},
    ],
)
def test_invalid_prediction_records_are_refused(
    tmp_path: Path, bad_record: dict[str, object]
) -> None:
    path = tmp_path / "predictions.jsonl"
    _write_jsonl(path, [bad_record])
    with pytest.raises(ValueError):
        load_ocr_predictions(path)


def test_duplicate_json_keys_and_duplicate_query_ids_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "truth.jsonl"
    path.write_text(
        '{"query_id":"q","query_id":"changed","scenario":"crop","visible_codes":[]}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Repeated JSON field"):
        load_ocr_truth(path)

    _write_jsonl(
        path,
        [
            {"query_id": "q", "scenario": "crop", "visible_codes": []},
            {"query_id": "q", "scenario": "weak_light", "visible_codes": []},
        ],
    )
    with pytest.raises(ValueError, match="Duplicate query_id"):
        load_ocr_truth(path)


def test_manifest_over_size_limit_is_refused_without_loading(tmp_path: Path) -> None:
    path = tmp_path / "large.jsonl"
    with path.open("wb") as handle:
        handle.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="16 MiB"):
        load_ocr_truth(path)


def test_cli_outputs_only_aggregates_and_manifest_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    truth_path = tmp_path / "truth.jsonl"
    pred_path = tmp_path / "predictions.jsonl"
    output_path = tmp_path / "metrics.json"
    _write_jsonl(
        truth_path,
        [{"query_id": "private-q17", "scenario": "crop", "visible_codes": ["PRIVATE-KA45"]}],
    )
    _write_jsonl(
        pred_path,
        [{"query_id": "private-q17", "extracted_codes": ["PRIVATE-KA45"]}],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_ocr",
            "--truth",
            str(truth_path),
            "--predictions",
            str(pred_path),
            "--output",
            str(output_path),
        ],
    )
    main()
    printed = capsys.readouterr().out
    result = json.loads(printed)
    assert result["task"] == "ocr_code_extraction"
    assert result["metrics"]["overall"]["code_recall"] == 1.0
    assert len(result["ground_truth_sha256"]) == 64
    assert "PRIVATE-KA45" not in printed
    assert "private-q17" not in printed
    assert output_path.read_text(encoding="utf-8").strip() == printed.strip()
