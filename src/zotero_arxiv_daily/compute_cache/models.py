"""Public cache values and strict on-disk schemas."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, ClassVar, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

type CacheValue = None | bool | int | float | str | list[CacheValue] | dict[str, CacheValue]
type FloatArray = NDArray[np.float32] | NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class CacheSettings:
    enabled: bool = False
    key: str | None = field(default=None, repr=False)
    path: Path = Path(".cache/zad-compute")
    budget_bytes: int = 8_000_000_000
    shard_bytes: int = 16_777_216
    ttl_days: int = 7
    model_revision: str | None = None


@dataclass(frozen=True, slots=True)
class ScoreSnapshot:
    scores: tuple[float, ...]
    order: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CacheStats:
    vector_hits: int = 0
    vector_misses: int = 0
    score_hits: int = 0
    score_misses: int = 0
    evictions: int = 0
    bytes_used: int = 0
    writes: int = 0


class StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class VectorRecord(StrictModel):
    version: Literal[1] = 1
    kind: Literal["vectors"] = "vectors"
    key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created: Annotated[float, Field(ge=0)]
    dtype: Literal["<f4", ">f4", "<f8", ">f8"]
    count: Annotated[int, Field(ge=0, le=1_000_000)]
    data: Annotated[str, Field(max_length=10_666_668)]


class ScoreRecord(StrictModel):
    version: Literal[1] = 1
    kind: Literal["scores"] = "scores"
    key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created: Annotated[float, Field(ge=0)]
    scores: Annotated[tuple[float, ...], Field(max_length=1_000_000)]
    order: Annotated[tuple[int, ...], Field(max_length=1_000_000)]


type Record = VectorRecord | ScoreRecord


class Entry(StrictModel):
    name: Annotated[str, Field(pattern=r"^shard-[0-9a-f]{32}\.enc$")]
    kind: Literal["vectors", "scores"]
    created: Annotated[float, Field(ge=0)]
    touched: Annotated[float, Field(ge=0)]


class Manifest(StrictModel):
    version: Literal[1] = 1
    entries: Annotated[dict[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], Entry], Field(max_length=100_000)] = Field(default_factory=dict)
