"""Normalize publication identifiers at external-data boundaries."""

import re
from typing import Final


_DOI_PREFIXES: Final[tuple[str, ...]] = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "dx.doi.org/",
    "doi.org/",
)
_DOI_PATTERN: Final[re.Pattern[str]] = re.compile(r"10\.\d{4,9}/\S+")
_ISSN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<first>\d{4})-?(?P<second>\d{3}[\dXx])"
)


def normalize_doi(value: str | None) -> str | None:
    """Return a canonical DOI or None when the value is not a DOI."""
    if value is None:
        return None

    normalized = value.strip().casefold()
    for prefix in _DOI_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break

    return normalized if _DOI_PATTERN.fullmatch(normalized) else None


def normalize_issn(value: str) -> str | None:
    """Return a checksum-valid ISSN in NNNN-NNNX form or None."""
    match = _ISSN_PATTERN.fullmatch(value.strip())
    if match is None:
        return None

    compact = f"{match.group('first')}{match.group('second')}".upper()
    weighted_sum = sum(
        int(digit) * weight
        for digit, weight in zip(compact[:7], range(8, 1, -1), strict=True)
    )
    check_value = (11 - weighted_sum % 11) % 11
    expected_check_digit = "X" if check_value == 10 else str(check_value)
    if compact[-1] != expected_check_digit:
        return None

    return f"{compact[:4]}-{compact[4:]}"
