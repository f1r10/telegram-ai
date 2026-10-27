"""Mobile photo lookup uses active human-confirmed references and abstains safely."""

import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from PIL import Image, ImageDraw
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.infrastructure.database.models import ImageAssetOrigin
from office_assistant.miniapp.photo_lookup import InvalidLookupPhoto, MiniAppPhotoLookup
from office_assistant.processing.document_visual_candidates import DocumentImageSource
from office_assistant.processing.semantic_visual import (
    CLIP_DIMENSION,
    SemanticVisualSearch,
)
from office_assistant.repositories.product_image_relations import ProductImageRelationRepository


def picture(color: tuple[int, int, int], *, side: bool = False) -> bytes:
    image = Image.new("RGB", (128, 128), color)
    draw = ImageDraw.Draw(image)
    if side:
        draw.polygon([(12, 18), (110, 36), (38, 113)], fill="white")
        draw.ellipse((60, 40, 99, 94), fill="black")
    else:
        draw.rectangle((35, 12, 100, 111), fill="white")
        draw.line((10, 20, 113, 99), fill="black", width=7)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class FakeSession:
    def __init__(
        self,
        rows: list[tuple[str, str, str]],
        *,
        known_asset: bool = False,
        embedded_rows: list[tuple[int, int, str, str, dict[str, object], str, str]] | None = None,
    ) -> None:
        self.rows = rows
        self.embedded_rows = embedded_rows or []
        self._selected_rows: list[tuple[object, ...]] = []
        self.known_asset = known_asset
        self.executed = 0

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass

    async def scalar(self, _query: object) -> int | None:
        return 55 if self.known_asset else None

    async def execute(self, _query: object) -> "FakeSession":
        self.executed += 1
        self._selected_rows = (
            list(self.embedded_rows)
            if "image_asset_occurrences" in str(_query)
            else list(self.rows)
        )
        return self

    def all(self) -> list[tuple[object, ...]]:
        return self._selected_rows


class FakeFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSession:
        return self.session


class ColorEncoder:
    def embed(self, paths: list[Path]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for path in paths:
            with Image.open(path) as image:
                pixel = image.getpixel((0, 0))
                assert isinstance(pixel, tuple)
                rgb = cast(tuple[int, int, int], pixel)
                vectors.append([float(c) for c in rgb] + [0.0] * (CLIP_DIMENSION - 3))
        return vectors


class OfflineError:
    def embed(self, paths: list[Path]) -> list[list[float]]:
        del paths
        raise RuntimeError("No local CLIP weights")


def make_service(
    root: Path, session: FakeSession, *, semantic: SemanticVisualSearch | None = None
) -> MiniAppPhotoLookup:
    return MiniAppPhotoLookup(
        cast(async_sessionmaker[AsyncSession], FakeFactory(session)),
        storage_root=root,
        semantic=semantic,
    )


def test_exact_confirmation_is_explicit_active_and_never_claims_stock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = picture((200, 50, 20))

    async def active_only(
        self: ProductImageRelationRepository, digest: str, *, active_product_only: bool = False
    ) -> tuple[object, str] | None:
        del self
        assert digest == sha256(source).hexdigest()
        assert active_product_only is True
        return (object(), "KA45")

    monkeypatch.setattr(
        ProductImageRelationRepository, "active_confirmation_holder_by_content", active_only
    )
    session = FakeSession([])
    result = asyncio.run(make_service(tmp_path, session).lookup(source))
    assert result.status == "exact"
    assert result.codes == ("KA45",)
    assert result.method == "confirmed_sha256"
    assert "price" not in result.payload()
    assert "stock" not in result.payload()
    assert session.executed == 0


def test_cross_angle_suggests_not_identifies_and_unknown_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_holder(
        self: ProductImageRelationRepository, digest: str, *, active_product_only: bool = False
    ) -> None:
        del self, digest
        assert active_product_only is True
        return None

    monkeypatch.setattr(
        ProductImageRelationRepository, "active_confirmation_holder_by_content", no_holder
    )
    root = tmp_path / "photos"
    root.mkdir()
    a = picture((225, 20, 20))
    b = picture((20, 20, 225))
    (root / "a.png").write_bytes(a)
    (root / "b.png").write_bytes(b)
    rows = [
        ("KA45", "a.png", sha256(a).hexdigest()),
        ("KA46", "b.png", sha256(b).hexdigest()),
    ]
    model = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=tmp_path / "cache",
        encoder=ColorEncoder(),
        min_score=0.87,
    )
    svc = make_service(root, FakeSession(rows), semantic=model)
    result = asyncio.run(svc.lookup(picture((225, 20, 20), side=True)))
    assert result.status == "candidates"
    assert result.codes == ("KA45",)
    assert result.method == "cross_angle"
    assert "təsdiqləmir" in result.notice
    unknown = asyncio.run(svc.lookup(picture((20, 225, 20), side=True)))
    assert unknown.status == "not_found"
    assert unknown.codes == ()


def test_only_near_candidates_without_model_never_auto_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_holder(
        self: ProductImageRelationRepository, digest: str, *, active_product_only: bool = False
    ) -> None:
        del self, digest, active_product_only
        return None

    monkeypatch.setattr(
        ProductImageRelationRepository, "active_confirmation_holder_by_content", no_holder
    )
    source = picture((200, 35, 50))
    (tmp_path / "ref.png").write_bytes(source)
    sess = FakeSession([("KA45", "ref.png", sha256(source).hexdigest())])
    result = asyncio.run(make_service(tmp_path, sess).lookup(source))
    assert result.status == "candidates"
    assert result.method == "near_duplicate"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not-an-image",
        b"\x89PNG\r\n\x1a\n" + b"junk",
        b"x" * (4 * 1024 * 1024 + 1),
    ],
)
def test_invalid_photographs_do_not_start_database_lookup(tmp_path: Path, body: bytes) -> None:
    session = FakeSession([])
    with pytest.raises(InvalidLookupPhoto):
        asyncio.run(make_service(tmp_path, session).lookup(body))
    assert session.executed == 0


def test_missing_offline_model_stays_unavailable_without_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_holder(
        self: ProductImageRelationRepository, digest: str, *, active_product_only: bool = False
    ) -> None:
        del self, digest, active_product_only
        return None

    monkeypatch.setattr(
        ProductImageRelationRepository, "active_confirmation_holder_by_content", no_holder
    )
    data = picture((220, 35, 20))
    (tmp_path / "r.png").write_bytes(data)
    session = FakeSession([("KA45", "r.png", sha256(data).hexdigest())])
    model = SemanticVisualSearch(
        storage_root=tmp_path,
        model_cache_root=tmp_path / "cache",
        encoder=OfflineError(),
    )
    result = asyncio.run(
        make_service(tmp_path, session, semantic=model).lookup(picture((220, 35, 20), side=True))
    )
    assert result.status == "unavailable"
    assert result.codes == ()


def test_mobile_near_match_rejects_mutated_approved_photo_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No product code is suggested from a file whose DB digest is stale."""

    async def no_holder(
        self: ProductImageRelationRepository, digest: str, *, active_product_only: bool = False
    ) -> None:
        del self, digest
        assert active_product_only
        return None

    monkeypatch.setattr(
        ProductImageRelationRepository, "active_confirmation_holder_by_content", no_holder
    )
    approved = picture((220, 40, 15))
    ref_path = tmp_path / "approved.png"
    ref_path.write_bytes(approved)
    session = FakeSession([("KA45", "approved.png", sha256(approved).hexdigest())])
    service = make_service(tmp_path, session)

    assert asyncio.run(service.lookup(approved)).codes == ("KA45",)
    # A replacement photograph still visually matches itself. Previously this
    # offered KA45 despite no longer matching the human-confirmed asset hash.
    changed = picture((210, 40, 15))
    ref_path.write_bytes(changed)
    result = asyncio.run(service.lookup(changed))
    assert result.status == "not_found"
    assert result.codes == ()


def test_mobile_returns_document_sources_without_product_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_holder(
        self: ProductImageRelationRepository, digest: str, *, active_product_only: bool = False
    ) -> None:
        del self, digest, active_product_only
        return None

    monkeypatch.setattr(
        ProductImageRelationRepository, "active_confirmation_holder_by_content", no_holder
    )
    body = picture((180, 60, 20))
    (tmp_path / "embedded.png").write_bytes(body)
    record = DocumentImageSource(
        40,
        99,
        "qiymetler.xlsx",
        ImageAssetOrigin.XLSX_EMBEDDED,
        {"sheet": "Mallar", "anchor_cell": "C7"},
        "embedded.png",
        sha256(body).hexdigest(),
    )
    sess = FakeSession(
        [],
        embedded_rows=[
            (
                record.occurrence_id,
                record.source_file_id,
                record.filename,
                record.origin,
                record.locator,
                record.storage_key,
                record.content_sha256,
            )
        ],
    )
    result = asyncio.run(make_service(tmp_path, sess).lookup(body))
    assert result.status == "source_candidates"
    assert result.codes == ()
    assert result.method == "embedded_document"
    payload = result.payload()
    assert payload["codes"] == []
    assert payload["sources"] == [
        {
            "filename": "qiymetler.xlsx",
            "type": "XLSX",
            "location": "vərəq Mallar, xana C7",
            "method": "near_duplicate",
        }
    ]
    assert "stok deyil" in result.notice
