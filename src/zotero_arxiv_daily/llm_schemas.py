"""Bounded bilingual digest contract at the LLM response boundary."""

from typing import Annotated, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

type LlmValue = (
    str | int | float | bool | None | list["LlmValue"] | dict[str, "LlmValue"]
)

MAX_TITLE_CHARS: Final = 2048
MAX_ABSTRACT_CHARS: Final = 24000
MAX_TLDR_CHARS: Final = 2000
MAX_AFFILIATION_CHARS: Final = 256
MAX_AFFILIATIONS: Final = 100
MAX_RESPONSE_CHARS: Final = 100000

type DigestTitle = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=MAX_TITLE_CHARS)]
type DigestAbstract = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=MAX_ABSTRACT_CHARS)]
type DigestTldr = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=MAX_TLDR_CHARS)]
type DigestAffiliation = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=MAX_AFFILIATION_CHARS)]


class BilingualPaperDigest(BaseModel):
    """An all-or-nothing validated response; empty abstracts require absent source."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="forbid")

    title_en: DigestTitle
    title_zh: DigestTitle
    abstract_en: DigestAbstract
    abstract_zh: DigestAbstract
    tldr_en: DigestTldr
    tldr_zh: DigestTldr
    affiliations: Annotated[list[DigestAffiliation], Field(max_length=MAX_AFFILIATIONS)]


BILINGUAL_PAPER_DIGEST_JSON_SCHEMA: Final[dict[str, "LlmValue"]] = {
    "type": "object",
    "properties": {
        "title_en": {"type": "string", "minLength": 1, "maxLength": MAX_TITLE_CHARS},
        "title_zh": {"type": "string", "minLength": 1, "maxLength": MAX_TITLE_CHARS},
        "abstract_en": {"type": "string", "maxLength": MAX_ABSTRACT_CHARS},
        "abstract_zh": {"type": "string", "maxLength": MAX_ABSTRACT_CHARS},
        "tldr_en": {"type": "string", "minLength": 1, "maxLength": MAX_TLDR_CHARS},
        "tldr_zh": {"type": "string", "minLength": 1, "maxLength": MAX_TLDR_CHARS},
        "affiliations": {
            "type": "array", "maxItems": MAX_AFFILIATIONS,
            "items": {"type": "string", "minLength": 1, "maxLength": MAX_AFFILIATION_CHARS},
        },
    },
    "required": ["title_en", "title_zh", "abstract_en", "abstract_zh", "tldr_en", "tldr_zh", "affiliations"],
    "additionalProperties": False,
}
