"""Immutable presentation values shared by paper enrichment and email rendering."""

from dataclasses import dataclass, field
from typing import Literal

__all__ = (
    "BilingualText",
    "ImageMediaType",
    "InlineImage",
    "PaperPresentation",
    "RenderedEmail",
    "ResearchFigure",
)

type ImageMediaType = Literal["image/png", "image/jpeg"]


@dataclass(frozen=True, slots=True)
class BilingualText:
    en: str | None = None
    zh: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchFigure:
    data: bytes
    media_type: ImageMediaType
    caption: str
    source_url: str


@dataclass(frozen=True, slots=True)
class PaperPresentation:
    title: BilingualText = field(default_factory=BilingualText)
    abstract: BilingualText = field(default_factory=BilingualText)
    tldr: BilingualText = field(default_factory=BilingualText)
    first_figure: ResearchFigure | None = None
    bilingual_enabled: bool = True


@dataclass(frozen=True, slots=True)
class InlineImage:
    cid: str
    data: bytes
    media_type: ImageMediaType


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    html: str
    inline_images: tuple[InlineImage, ...] = ()
    plain_text: str = ""
