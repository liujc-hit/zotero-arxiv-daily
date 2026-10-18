"""Offline contracts for final-selection presentation and successful delivery."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from omegaconf import DictConfig, OmegaConf
from tests import make_recording_client

from zotero_arxiv_daily import final_enrichment as enrichment
from zotero_arxiv_daily.paper_presentation import (
    BilingualText, InlineImage, PaperPresentation, RenderedEmail, ResearchFigure,
)
from zotero_arxiv_daily.protocol import CorpusPaper, Paper
from zotero_arxiv_daily.presentation_config import (
    InvalidPresentationSwitchError, PresentationOptions,
)


def paper(title: str, score: float = 5.0) -> Paper:
    return Paper("arxiv", title, ["Author"], "Original abstract",
                 f"https://example.org/{title}", full_text="Full text", score=score)


@pytest.mark.parametrize("bilingual,figures", [(True, True), (True, False), (False, True), (False, False)])
def test_independent_features_when_enriching(monkeypatch: pytest.MonkeyPatch, bilingual: bool, figures: bool) -> None:
    # Given independent optional adapters and a legacy digest adapter.
    calls: list[str] = []
    selected = paper("Original")
    selected.doi = "10.1234/unchanged"
    selected.publisher = "Publisher"
    selected.pinned = True

    def generate(p: Paper, client, params) -> PaperPresentation:
        calls.append("bilingual")
        p.tldr = "Digest"
        p.affiliations = ["Institution"]
        return PaperPresentation(title=BilingualText(en=p.title, zh="译名"))

    def legacy(p: Paper, client, params) -> tuple[str, list[str]]:
        calls.append("legacy")
        p.tldr = "Legacy digest"
        return p.tldr, []

    def fetch(p: Paper) -> ResearchFigure:
        calls.append("figure")
        return ResearchFigure(b"image", "image/png", "Caption", p.url)

    monkeypatch.setattr(enrichment, "generate_bilingual_digest", generate)
    monkeypatch.setattr(enrichment, "fetch_first_figure", fetch)
    monkeypatch.setattr(Paper, "generate_tldr_and_affiliations", legacy)
    # When enriching the selection.
    result = enrichment.enrich_final_papers([selected], {}, make_recording_client()[0], {}, 8,
                                            bilingual=bilingual, figures=figures)
    # Then adapters honor their own flag without changing identity or ranking inputs.
    assert calls == (["bilingual"] if bilingual else ["legacy"]) + (["figure"] if figures else [])
    assert result[0] is selected
    assert (selected.title, selected.abstract, selected.score, selected.url) == (
        "Original", "Original abstract", 5.0, "https://example.org/Original")
    assert bool(selected.presentation and selected.presentation.first_figure) is figures
    assert (selected.doi, selected.publisher, selected.pinned) == ("10.1234/unchanged", "Publisher", True)


def test_optional_failures_when_generating_preserve_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given optional adapter failures that contain private response data.
    selected = paper("Original")
    logs: list[str] = []
    sink = enrichment.logger.add(logs.append, format="{message}")

    def fail(*args):
        raise RuntimeError("secret-response-and-url-token")

    monkeypatch.setattr(enrichment, "generate_bilingual_digest", fail)
    monkeypatch.setattr(enrichment, "fetch_first_figure", fail)
    try:
        # When both optional adapters fail.
        result = enrichment.enrich_final_papers([selected], {}, make_recording_client()[0], {}, 4,
                                                bilingual=True, figures=True)
    finally:
        enrichment.logger.remove(sink)
    # Then the paper survives with its original abstract digest and redacted diagnostics.
    assert result == [selected]
    assert selected.tldr == selected.abstract
    assert selected.presentation is not None
    assert selected.presentation.first_figure is None
    assert len(logs) == 2
    assert all("secret-response-and-url-token" not in line for line in logs)


def test_figure_budget_when_completion_is_reversed_uses_email_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given reversed completion from the executor and three 3-MiB figures.
    selected = [paper(str(i)) for i in range(3)]

    class ReversedExecutor:
        def __init__(self, *, max_workers: int):
            assert max_workers == 3

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def map(self, function, papers):
            completed = [function(p) for p in reversed(papers)]
            return reversed(completed)

    monkeypatch.setattr(enrichment, "ThreadPoolExecutor", ReversedExecutor)
    monkeypatch.setattr(Paper, "generate_tldr_and_affiliations", lambda *args: ("Digest", []))
    monkeypatch.setattr(enrichment, "fetch_first_figure", lambda p: ResearchFigure(
        b"x" * (3 * 1024 * 1024), "image/png", "Caption", p.url))
    # When enriching concurrently.
    result = enrichment.enrich_final_papers(selected, {}, make_recording_client()[0], {}, 20, figures=True)
    # Then the 8-MiB budget favors the first two email entries, not completion order.
    assert [p.title for p in result] == ["0", "1", "2"]
    assert [bool(p.presentation and p.presentation.first_figure) for p in result] == [True, True, False]


@pytest.fixture
def pipeline(config: DictConfig, monkeypatch: pytest.MonkeyPatch):
    from zotero_arxiv_daily import executor as execution

    config.email.bilingual.enabled = True
    config.email.figures.enabled = True
    config.executor.max_paper_num = 1
    config.executor.pin_keywords = ["Pinned"]
    # Score must meet the independent pinned_min_score floor (4.9) to be pinned.
    candidates = [paper("Top", 9), paper("Dropped", 8), paper("Pinned", 5)]
    instance = execution.Executor.__new__(execution.Executor)
    instance.config = config
    monkeypatch.setattr(instance, "openai_client", make_recording_client()[0], raising=False)
    instance.retrievers = {}
    monkeypatch.setattr(instance, "pipeline_enrichers", SimpleNamespace(enrich_before_rerank=lambda papers: None), raising=False)
    monkeypatch.setattr(instance, "reranker", SimpleNamespace(rerank=lambda papers, corpus: papers), raising=False)
    events: list[str] = []
    saved: list[set[str]] = []

    def save(identities: set[str]) -> None:
        events.append("save")
        saved.append(identities)

    monkeypatch.setattr(instance, "sent_doi_state_store", SimpleNamespace(load=lambda: set(), save=save), raising=False)
    monkeypatch.setattr(instance, "fetch_zotero_corpus", lambda: [
        CorpusPaper("Library", "Abstract", datetime(2026, 1, 1), [])])
    monkeypatch.setattr(instance, "filter_corpus", lambda papers: papers)
    monkeypatch.setattr(execution, "retrieve_and_merge", lambda retrievers: candidates)
    return instance, candidates, events, saved


@pytest.mark.parametrize("delivery_fails", [False, True])
def test_selected_only_when_sending_bundle_saves_only_after_success(pipeline, monkeypatch: pytest.MonkeyPatch, delivery_fails: bool) -> None:
    from zotero_arxiv_daily import executor as execution

    # Given pinned + top-N selection and deterministic presentation adapters.
    instance, candidates, events, saved = pipeline
    generated: list[str] = []
    fetched: list[str] = []
    rendered: list[Paper] = []
    image = InlineImage("figure", b"png", "image/png")

    def generate(p: Paper, client, params) -> PaperPresentation:
        generated.append(p.title)
        p.tldr = "Digest"
        return PaperPresentation(title=BilingualText(en=p.title, zh="译名"))

    def fetch(p: Paper) -> ResearchFigure:
        fetched.append(p.title)
        return ResearchFigure(b"png", "image/png", "Caption", p.url)

    def render(papers: list[Paper]) -> RenderedEmail:
        rendered.extend(papers)
        return RenderedEmail("<html>digest</html>", (image,), "Plain digest")

    def send(config, html: str, *, inline_images=(), plain_text="") -> None:
        assert (html, inline_images, plain_text) == ("<html>digest</html>", (image,), "Plain digest")
        events.append("send")
        if delivery_fails:
            raise OSError("SMTP unavailable")

    monkeypatch.setattr(enrichment, "generate_bilingual_digest", generate)
    monkeypatch.setattr(enrichment, "fetch_first_figure", fetch)
    monkeypatch.setattr(execution, "render_email_bundle", render)
    monkeypatch.setattr(execution, "send_email", send)
    # When running the real selection, enrichment and delivery orchestration.
    if delivery_fails:
        with pytest.raises(OSError, match="SMTP unavailable"):
            instance.run()
    else:
        instance.run()
    # Then only final papers receive optional work; sent state follows successful SMTP.
    assert set(generated) == set(fetched) == {"Pinned", "Top"}
    assert rendered == [candidates[2], candidates[0]]
    assert [p.title for p in candidates] == ["Top", "Dropped", "Pinned"]
    assert [p.score for p in candidates] == [9, 8, 5]
    assert candidates[2].pinned and candidates[1].presentation is None
    assert events == (["send"] if delivery_fails else ["send", "save"])
    assert saved == ([] if delivery_fails else [{"url:https://example.org/Pinned", "url:https://example.org/Top"}])


def test_production_defaults_when_loading_base_are_enabled() -> None:
    # Given the production base, independent from the legacy opt-out fixture.
    base = Path(__file__).resolve().parents[1] / "config" / "base.yaml"
    # When composing defaults.
    config = OmegaConf.load(base)
    # Then both new features default on.
    assert config.email.bilingual.enabled is True
    assert config.email.figures.enabled is True


@pytest.mark.parametrize("bilingual,figures", [(True, False), (False, True), (False, False)])
def test_switches_when_strings_parse_independently(bilingual: bool, figures: bool) -> None:
    # Given string values as supplied by environment-backed overlays.
    config = OmegaConf.create({"email": {
        "bilingual": {"enabled": str(bilingual)}, "figures": {"enabled": str(figures)}}})
    # When parsing the configuration boundary.
    options = PresentationOptions.from_config(config)
    # Then false strings never become truthy and switches remain independent.
    assert options == PresentationOptions(bilingual, figures)


def test_switches_when_invalid_fail_before_optional_work() -> None:
    # Given an invalid switch rather than a boolean.
    config = OmegaConf.create({"email": {"figures": {"enabled": "sometimes"}}})
    # When parsing, then reject the invalid value rather than silently enabling it.
    with pytest.raises(InvalidPresentationSwitchError, match="email.figures.enabled"):
        PresentationOptions.from_config(config)


def test_client_when_constructing_executor_has_bounded_timeout(config: DictConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    from zotero_arxiv_daily import executor as execution

    # Given offline source/reranker constructors (OpenAI construction sends nothing).
    monkeypatch.setattr(execution, "_build_retrievers", lambda config: {})
    monkeypatch.setattr(execution, "get_reranker_cls", lambda name: lambda config: SimpleNamespace())
    # When constructing the production executor and its real SDK client.
    instance = execution.Executor(config)
    # Then SDK requests have a finite timeout and cannot retry invisibly.
    try:
        assert instance.openai_client.timeout == 45.0
        assert instance.openai_client.max_retries == 0
    finally:
        instance.openai_client.close()


def test_worker_cap_when_many_papers_limits_optional_work(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given more selected papers and requested workers than the concurrency bound.
    observed: list[int] = []
    real_executor = enrichment.ThreadPoolExecutor

    def pool(*, max_workers: int):
        observed.append(max_workers)
        return real_executor(max_workers=max_workers)

    monkeypatch.setattr(enrichment, "ThreadPoolExecutor", pool)
    monkeypatch.setattr(enrichment, "generate_bilingual_digest", lambda *args: PaperPresentation())
    # When optional enrichment runs for a larger selection.
    enrichment.enrich_final_papers([paper(str(i)) for i in range(8)], {},
                                  make_recording_client()[0], {}, 20, bilingual=True)
    # Then no more than four workers are admitted.
    assert observed == [4]


def test_legacy_callback_when_bundle_has_no_extras(pipeline, monkeypatch: pytest.MonkeyPatch) -> None:
    from zotero_arxiv_daily import executor as execution

    # Given enabled features whose bundle contains only HTML.
    instance, _, events, _ = pipeline
    monkeypatch.setattr(instance, "_enrich_papers", lambda papers: None)
    monkeypatch.setattr(execution, "render_email_bundle", lambda papers: RenderedEmail("HTML"))
    monkeypatch.setattr(execution, "send_email", lambda config, html: events.append(html))
    # When sending with a legacy two-positional-argument callback.
    instance.run()
    # Then no new keyword arguments are required for an HTML-only bundle.
    assert events == ["HTML", "save"]


def test_real_bundle_when_bilingual_generation_succeeds(pipeline, monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    import json
    from zotero_arxiv_daily import executor as execution

    # Given a real bilingual generator, renderer and a local structured SDK response
    # carrying the five contract keys (titles, TLDRs, affiliations; no abstract fields).
    instance, _, events, saved = pipeline
    instance.config.llm.api_mode = "chat_completion"
    instance.config.llm.thinking = None
    client, calls = make_recording_client(json.dumps({
        "title_en": "Title", "title_zh": "译名", "tldr_en": "Digest", "tldr_zh": "中文总结",
        "affiliations": ["Institution"],
    }))
    instance.openai_client = client
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=")
    monkeypatch.setattr(enrichment, "fetch_first_figure", lambda p: ResearchFigure(
        png, "image/png", "Research plot", "https://arxiv.org/html/2601.00001/figure.png"))

    def send(config, html: str, *, inline_images=(), plain_text="") -> None:
        assert "译名" in html and "cid:" in html
        assert "中文总结" in html and "中文总结" in plain_text
        assert "Original abstract" not in html and "Original abstract" not in plain_text
        assert len(inline_images) == 2
        assert all(image.data == png for image in inline_images)
        events.append("send")

    monkeypatch.setattr(execution, "send_email", send)
    # When running through final selection to the actual email bundle.
    instance.run()
    # Then one request per selected paper reaches rich delivery before state persistence.
    assert len(calls) == 2
    assert events == ["send", "save"] and len(saved) == 1
