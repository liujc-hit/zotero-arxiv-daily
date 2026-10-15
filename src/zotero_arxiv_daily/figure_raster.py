"""Verify bounded original PNG/JPEG payloads without transforming their bytes."""

from typing import assert_never
import zlib

import pymupdf

from .figure_transport import LIMIT, Resource
from .paper_presentation import ImageMediaType, ResearchFigure


def _png_size(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        return None
    position = 8
    compressed: list[bytes] = []
    while position + 12 <= len(data):
        size = int.from_bytes(data[position:position + 4])
        end = position + size + 12
        if end > len(data):
            return None
        chunk = data[position + 4:end - 4]
        if zlib.crc32(chunk) != int.from_bytes(data[end - 4:end]):
            return None
        if chunk[:4] == b"IDAT":
            compressed.append(chunk[4:])
        if chunk[:4] == b"IEND":
            if end != len(data) or not compressed:
                return None
            width = int.from_bytes(data[16:20])
            height = int.from_bytes(data[20:24])
            if not 0 < width * height <= 16_000_000:
                return None
            # Maximum 16-bit RGBA scanlines plus generous Adam7 row overhead.
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(b"".join(compressed), width * height * 8 + height * 8 + 1024)
            if not decoder.eof or decoder.unused_data or not decoded:
                return None
            return width, height
        position = end
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        return None
    position = 2
    while position + 4 <= len(data):
        if data[position] != 255:
            return None
        position += 1
        while position < len(data) and data[position] == 255:
            position += 1
        marker = data[position]
        position += 1
        size = int.from_bytes(data[position:position + 2])
        if size < 2 or position + size > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2} and size >= 8:
            height = int.from_bytes(data[position + 3:position + 5])
            width = int.from_bytes(data[position + 5:position + 7])
            return width, height
        position += size
    return None


def verified_figure(resource: Resource, caption: str) -> ResearchFigure | None:
    """Only trusted raster formats with bounded decoded dimensions leave here."""
    data = resource.data
    if not data or len(data) > LIMIT:
        return None
    try:
        supported: dict[str, ImageMediaType] = {"image/png": "image/png", "image/jpeg": "image/jpeg"}
        media = supported.get(resource.media_type)
        match media:
            case None:
                return None
            case "image/png":
                size = _png_size(data)
            case "image/jpeg":
                size = _jpeg_size(data)
            case unreachable:
                assert_never(unreachable)
        if size is None or min(size) <= 0 or size[0] * size[1] > 16_000_000:
            return None
        pixmap = pymupdf.Pixmap(data)
        if (pixmap.width, pixmap.height) != size:
            return None
        return ResearchFigure(data, media, caption, resource.url)
    except (ValueError, RuntimeError, pymupdf.mupdf.FzErrorBase, zlib.error, IndexError):
        return None
