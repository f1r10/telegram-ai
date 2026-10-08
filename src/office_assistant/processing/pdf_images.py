"""What one PDF image stream actually holds, and what may be stored as a picture (§23.3, §23.5).

A PDF image XObject carries either a whole picture file (a JPEG, a JPEG 2000) or the raw colour
samples of the grid its own dictionary declares. The first is stored as it arrives. The second needs
a header and a row filter byte before anything downstream can read it, which is arithmetic over the
declared grid — deterministic, but only while it comes out exactly. Where it does not, this module
names the reason and stores nothing: a picture rebuilt from a guess about colour space, bit depth or
row predictors would be a forgery that search results then cite as the document's own (§23.5).
"""

import zlib
from typing import Any, cast

# Filters that leave a complete, independently readable picture behind.
WHOLE_PICTURE_FILTERS = frozenset({"/DCTDecode", "/JPXDecode"})
# Filters that only undo a compression, leaving a flat rectangle of samples to interpret.
SAMPLE_FILTERS = frozenset({"/FlateDecode", "/LZWDecode", "/RunLengthDecode"})
# A stream with no /Filter at all already holds those samples, so it needs no decoder.
UNFILTERED = "unfiltered"

_GRAY = "/DeviceGray"
_RGB = "/DeviceRGB"
_COMPONENTS = {_GRAY: 1, _RGB: 3}
_BASE_COLOR_TYPE = {_GRAY: 0, _RGB: 2}
_BITS_PER_COMPONENT = 8
_PNG_START = b"\x89PNG\r\n\x1a\n"

# Entries that stand between the stored samples and the colours a page paints. Applying them would
# mean owning a colour management system, so a stream carrying one is refused by name.
_COLOR_TRANSFORMS = ("/Decode", "/Mask")


class SampleImageError(ValueError):
    """One image stream this build refuses to turn into a picture, and why."""


def pdf_name(value: Any) -> str:
    """A PDF name as a plain string, resolving an indirect reference first."""

    if value is None:
        return ""
    resolved = value.get_object() if hasattr(value, "get_object") else value
    text = str(resolved)
    return "" if text in {"None", "null"} else text


def pdf_integer(value: Any) -> int | None:
    """An integer entry, or ``None`` when the dictionary omits or mangles it."""

    resolved = value.get_object() if hasattr(value, "get_object") else value
    try:
        return int(resolved)
    except (TypeError, ValueError):
        return None


def stream_filters(value: Any) -> list[str]:
    """The `/Filter` entries of a stream, whether the dictionary wrote one name or an array."""

    resolved = value.get_object() if hasattr(value, "get_object") else value
    if resolved is None:
        return []
    if isinstance(resolved, (list, tuple)):
        entries = cast("list[Any] | tuple[Any, ...]", resolved)
        return [pdf_name(item) for item in entries]
    return [pdf_name(resolved)]


def picture_bytes(stream: Any, raw_limit: int) -> bytes:
    """The bytes of the picture this image stream holds, ready to describe and store.

    ``raw_limit`` bounds a *declared* grid before any of it is inflated, so a dictionary promising
    a 50 000 by 50 000 rectangle is refused rather than decompressed to find out.
    """

    filters = stream_filters(stream.get("/Filter"))
    if len(filters) == 1 and filters[0] in WHOLE_PICTURE_FILTERS:
        return _opened(stream, filters[0])
    return _flattened_png(stream, filters, raw_limit)


def soft_mask_note(stream: Any) -> str | None:
    """A warning for a picture whose stored bytes cannot carry the transparency the page paints.

    A JPEG keeps its own bytes exactly as the document holds them, which is the point of §23.3, so
    a mask travelling with it cannot be folded in. What the mask is doing there has to be said out
    loud rather than left for a reader to discover (§23.5). A flat stream either carries its mask
    into the picture or is refused, so it gets no note.
    """

    if stream.get("/SMask") is None:
        return None
    filters = stream_filters(stream.get("/Filter"))
    if len(filters) == 1 and filters[0] in WHOLE_PICTURE_FILTERS:
        return "its soft mask is not carried into the picture file that was stored"
    return None


def _flattened_png(stream: Any, filters: list[str], raw_limit: int) -> bytes:
    if _flag(stream.get("/ImageMask")):
        raise SampleImageError("a stencil mask is not a picture")
    decoder = _sample_decoder(filters)
    color_space = pdf_name(stream.get("/ColorSpace"))
    components = _COMPONENTS.get(color_space)
    if components is None:
        named = color_space or "none"
        raise SampleImageError(f"colour space {named} needs a mapping nobody declared")
    depth = pdf_integer(stream.get("/BitsPerComponent"))
    if depth != _BITS_PER_COMPONENT:
        raise SampleImageError(
            f"bit depth {depth if depth is not None else 'unset'} is not unpacked here"
        )
    width = pdf_integer(stream.get("/Width"))
    height = pdf_integer(stream.get("/Height"))
    if not width or not height:
        raise SampleImageError("dictionary declares no usable /Width and /Height grid")
    stride = width * components
    if stride * height > raw_limit:
        raise SampleImageError(f"declared grid {width}x{height} is over the {raw_limit} byte bound")
    for entry in _COLOR_TRANSFORMS:
        if stream.get(entry) is not None:
            raise SampleImageError(f"{entry} stands between these samples and the painted colour")
    samples = _opened(stream, decoder)
    if len(samples) != stride * height:
        raise SampleImageError(
            f"decoded samples are {len(samples)} bytes, not the {stride * height} the grid declares"
        )
    alpha = _soft_mask(stream, width, height, raw_limit)
    if alpha is None:
        return _encode_png(width, height, _BASE_COLOR_TYPE[color_space], samples, stride)
    painted = b"".join(
        samples[pixel * components : pixel * components + components] + alpha[pixel : pixel + 1]
        for pixel in range(width * height)
    )
    return _encode_png(width, height, _BASE_COLOR_TYPE[color_space] + 4, painted, stride + width)


def _sample_decoder(filters: list[str]) -> str:
    """The single step that leaves a flat rectangle of samples, named for the refusal it needs."""

    if len(filters) > 1:
        raise SampleImageError(f"filter chain {'+'.join(filters)} is not one encoder")
    if not filters:
        return UNFILTERED
    if filters[0] not in SAMPLE_FILTERS:
        raise SampleImageError(f"stream filter {filters[0]} holds samples no rule here reinterpret")
    return filters[0]


def _soft_mask(stream: Any, width: int, height: int, raw_limit: int) -> bytes | None:
    """The `/SMask` samples to carry as an alpha channel, or ``None`` when the picture has none.

    A mask that is present but unreadable is a refusal, not an omission: dropping it changes the
    picture, which is the one thing §23.5 does not forgive.
    """

    holder = stream.get("/SMask")
    if holder is None:
        return None
    mask = holder.get_object() if hasattr(holder, "get_object") else holder
    decoder = _sample_decoder(stream_filters(mask.get("/Filter")))
    grid = (pdf_integer(mask.get("/Width")), pdf_integer(mask.get("/Height")))
    if pdf_name(mask.get("/ColorSpace")) != _GRAY:
        raise SampleImageError("soft mask is not a plain gray rectangle")
    if pdf_integer(mask.get("/BitsPerComponent")) != _BITS_PER_COMPONENT:
        raise SampleImageError("soft mask is not 8-bit")
    if grid != (width, height):
        raise SampleImageError(f"soft mask covers grid {grid}, not the picture's {(width, height)}")
    if width * height > raw_limit:
        raise SampleImageError(f"soft mask grid is over the {raw_limit} byte bound")
    samples = _opened(mask, decoder)
    if len(samples) != width * height:
        raise SampleImageError("soft mask samples do not fill the declared grid")
    return samples


def _flag(value: Any) -> bool:
    """A boolean entry, tolerating the producers that write ``/true`` where PDF says ``true``."""

    return pdf_name(value).lstrip("/").lower() == "true"


def _opened(stream: Any, filter_name: str) -> bytes:
    try:
        return stream.get_data()
    except Exception as error:  # noqa: BLE001 - a stream the reader cannot open is a named refusal
        raise SampleImageError(
            f"{filter_name} stream will not decode ({type(error).__name__})"
        ) from error


def _encode_png(width: int, height: int, color_type: int, body: bytes, stride: int) -> bytes:
    rows = b"".join(
        b"\x00" + body[offset : offset + stride] for offset in range(0, len(body), stride)
    )
    header = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes([_BITS_PER_COMPONENT, color_type, 0, 0, 0])
    )
    return (
        _PNG_START
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(rows, 6))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")
