import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from office_assistant.scripts.benchmark_ocr import OcrPrediction
from office_assistant.scripts.run_local_ocr import (
    ImageCase,
    extract_known_codes,
    load_image_manifest,
    load_product_codes,
    produce_predictions,
    read_tesseract,
    resolve_private_image,
    write_private_predictions,
)


def _image(root: Path, name: str = "image.png") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(b"synthetic-fixture")
    return path


def test_product_codes_are_matched_conservatively() -> None:
    registry = ("KA45", "KA46", "KA4S")
    assert extract_known_codes("KA-45 və ka 46", registry) == ("KA45", "KA46")
    assert extract_known_codes("KA450 KA4S1", registry) == ()
    assert extract_known_codes("KA4S", registry) == ("KA4S",)
    assert extract_known_codes("müştəriyə KA_45 göndərildi", registry) == ("KA45",)


def test_manifest_codes_normalize_and_reject_canonical_duplicates(tmp_path: Path) -> None:
    codes = tmp_path / "codes.txt"
    codes.write_text("KA45\nka-46\n", encoding="utf-8")
    assert load_product_codes(codes) == ("KA45", "KA46")
    codes.write_text("KA45\nKA-45\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate canonical"):
        load_product_codes(codes)


@pytest.mark.parametrize(
    "image_name",
    [
        "../escape.png",
        "/etc/passwd",
        r"..\escape.png",
        "C:/private/company.png",
        "part/./image.png",
        "part//image.png",
        "part/../image.png",
        "file.txt",
        "file.png:extra",
        "\x00hidden.png",
    ],
)
def test_image_path_rejects_traversal_and_invalid_extensions(
    tmp_path: Path, image_name: str
) -> None:
    with pytest.raises(ValueError):
        resolve_private_image(tmp_path, image_name)


def test_symlink_outside_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    external = _image(tmp_path / "elsewhere")
    link = root / "linked.png"
    try:
        link.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable")
    with pytest.raises(ValueError, match="outside"):
        resolve_private_image(root, "linked.png")


def test_files_must_be_nonempty_and_under_size_cap(tmp_path: Path) -> None:
    _image(tmp_path).write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        resolve_private_image(tmp_path, "image.png")
    with (tmp_path / "image.png").open("wb") as handle:
        handle.truncate(25 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="25 MiB"):
        resolve_private_image(tmp_path, "image.png")


def test_generator_uses_relative_paths_and_emits_candidates_only(tmp_path: Path) -> None:
    root = tmp_path / "private"
    _image(root)
    seen: list[Path] = []

    def fake_reader(image: Path) -> str:
        seen.append(image)
        return "SKU KA-45, lookalike KA4S"

    result = produce_predictions(root, [ImageCase("q1", "image.png")], ("KA45",), fake_reader)
    assert result == [OcrPrediction("q1", ("KA45",))]
    assert seen == [root / "image.png"]


def test_invalid_generator_input_is_refused_before_ocr(tmp_path: Path) -> None:
    root = tmp_path / "private"
    _image(root)

    def no_call(image: Path) -> str:
        raise AssertionError("OCR must not run")

    with pytest.raises(ValueError, match="Duplicate query_id"):
        produce_predictions(
            root,
            [ImageCase("q", "image.png"), ImageCase("q", "image.png")],
            ("KA45",),
            no_call,
        )
    with pytest.raises(ValueError, match="safe text limit"):
        produce_predictions(
            root,
            [ImageCase("q", "image.png")],
            ("KA45",),
            lambda _: "a" * 1_000_001,
        )


def test_image_manifest_requires_explicit_fields_and_unique_ids(tmp_path: Path) -> None:
    manifest = tmp_path / "images.jsonl"
    manifest.write_text(
        '{"query_id":"q1","image":"one.png"}\n{"query_id":"q2","image":"two.jpg"}\n',
        encoding="utf-8",
    )
    assert load_image_manifest(manifest) == [
        ImageCase("q1", "one.png"),
        ImageCase("q2", "two.jpg"),
    ]
    manifest.write_text(
        '{"query_id":"q1","image":"one.png"}\n{"query_id":"q1","image":"two.png"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate query_id"):
        load_image_manifest(manifest)
    manifest.write_text('{"query_id":"q1","image":"one.png","private":"secret"}\n')
    with pytest.raises(ValueError, match="fields"):
        load_image_manifest(manifest)


def test_tesseract_runs_without_a_shell_or_exposing_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from office_assistant.scripts import run_local_ocr

    seen: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        seen["args"] = args
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="KA45 private document", stderr="")

    monkeypatch.setattr(run_local_ocr.subprocess, "run", fake_run)
    image = tmp_path / "image.png"
    output = read_tesseract(image, "tesseract", "eng+aze", 15)
    assert output == "KA45 private document"
    assert seen["args"] == [
        "tesseract",
        str(image),
        "stdout",
        "-l",
        "eng+aze",
        "--psm",
        "11",
    ]
    options = cast(dict[str, object], seen["kwargs"])
    assert options["capture_output"] is True
    assert options["timeout"] == 15
    assert "shell" not in options


@pytest.mark.parametrize("langs", ["eng --help", "-l", "eng;curl", ""])
def test_invalid_languages_cannot_become_cli_arguments(tmp_path: Path, langs: str) -> None:
    with pytest.raises(ValueError, match="language"):
        read_tesseract(tmp_path / "image.png", "tesseract", langs, 15)


def test_local_ocr_engine_failure_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from office_assistant.scripts import run_local_ocr

    monkeypatch.setattr(
        run_local_ocr.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="PRIVATE-KA45", stderr="secret path"
        ),
    )
    with pytest.raises(ValueError, match="Local OCR engine failed") as error:
        read_tesseract(tmp_path / "image.png", "tesseract", "eng", 30)
    assert "PRIVATE" not in str(error.value)
    assert "secret" not in str(error.value)


def test_output_is_atomic_and_matches_ocr_evaluator_schema(tmp_path: Path) -> None:
    from office_assistant.scripts.benchmark_ocr import load_ocr_predictions

    target = tmp_path / "results" / "ocr.jsonl"
    predictions = [OcrPrediction("q1", ("KA45",)), OcrPrediction("q2", ())]
    write_private_predictions(target, predictions)
    assert load_ocr_predictions(target) == predictions
    assert len(target.read_text(encoding="utf-8").splitlines()) == 2
    assert list(target.parent.glob(".ocr-*")) == []
    first = json.loads(target.read_text(encoding="utf-8").splitlines()[0])
    assert set(first) == {"query_id", "extracted_codes"}
