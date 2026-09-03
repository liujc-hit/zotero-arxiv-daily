"""Shared fixtures and test doubles for enrichment pipeline tests."""

from typing import Final, final

from omegaconf import DictConfig, OmegaConf

from zotero_arxiv_daily.protocol import Paper


OPENALEX_KEY: Final = "runtime-openalex-secret"


def enabled_config() -> DictConfig:
    return OmegaConf.create(
        {
            "enrichment": {"enabled": True},
            "source": {
                "openalex": {
                    "api_keys": [OPENALEX_KEY],
                    "allow_anonymous": False,
                    "lookback_days": 1,
                },
            },
            "reranker": {
                "venue_prestige": {
                    "enabled": True,
                    "weight": 0.2,
                    "max_multiplier": 2.0,
                }
            },
            "executor": {"debug": False},
        }
    )


def paper(title: str) -> Paper:
    return Paper(
        source="test",
        title=title,
        authors=[],
        abstract="",
        url=f"https://example.test/{title}",
    )


@final
class RecordingStage:
    def __init__(self, name: str, calls: list[tuple[str, int, tuple[int, ...]]]) -> None:
        self._name = name
        self._calls = calls

    def enrich(self, papers: list[Paper]) -> None:
        self._calls.append((self._name, id(papers), tuple(id(item) for item in papers)))


@final
class FailingVenue:
    def __init__(self, failure: Exception) -> None:
        self._failure = failure
        self.calls = 0

    def enrich(self, papers: list[Paper]) -> None:
        del papers
        self.calls += 1
        raise self._failure
