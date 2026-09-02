"""Strict contracts for batched PubMed XML metadata parsing."""

from typing import Final

import pytest

from zotero_arxiv_daily.retriever.pubmed_metadata import (
    PubMedInvalidXmlError,
    PubMedMetadataError,
    PubMedMissingPmidError,
    PubMedMissingTitleError,
    PubMedRecord,
    parse_pubmed_article_set,
)


PRIVATE_PMID: Final = "987654321"
PRIVATE_DOI: Final = "10.4242/private-record"


def test_parse_pubmed_article_set_maps_batched_mixed_content_and_fallbacks() -> None:
    # Given: two ordered articles covering preferred and fallback metadata paths.
    payload = b"""
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>111</PMID>
              <Article>
                <Journal>
                  <ISSN IssnType="Electronic">00280836</ISSN>
                  <Title>The <i>Journal</i> of Tests</Title>
                </Journal>
                <ArticleTitle>Alpha <i>beta</i>: <sup>2</sup> findings.</ArticleTitle>
                <ELocationID EIdType="doi" ValidYN="Y">10.9999/fallback</ELocationID>
                <Abstract>
                  <AbstractText Label="BACKGROUND">First <b>section</b>.</AbstractText>
                  <AbstractText>Second <i>section</i>.</AbstractText>
                </Abstract>
                <AuthorList>
                  <Author>
                    <LastName>Lovelace</LastName>
                    <ForeName>Ada</ForeName>
                  </Author>
                  <Author>
                    <CollectiveName>The <i>Consortium</i></CollectiveName>
                  </Author>
                </AuthorList>
                <PublicationTypeList>
                  <PublicationType UI="D000076942">Working Paper</PublicationType>
                </PublicationTypeList>
              </Article>
              <MedlineJournalInfo>
                <MedlineTA>Ignored fallback</MedlineTA>
                <ISSNLinking>2434561x</ISSNLinking>
              </MedlineJournalInfo>
            </MedlineCitation>
            <PubmedData>
              <ArticleIdList>
                <ArticleId IdType="doi">HTTPS://DOI.ORG/10.5555/Preferred</ArticleId>
              </ArticleIdList>
            </PubmedData>
          </PubmedArticle>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>222</PMID>
              <Article>
                <Journal>
                  <ISSN>invalid</ISSN>
                  <Title>   </Title>
                </Journal>
                <ArticleTitle>Fallback article</ArticleTitle>
                <ELocationID EIdType="doi" ValidYN="N">10.7777/rejected</ELocationID>
                <ELocationID EIdType="doi" ValidYN="Y">10.7777/Fallback</ELocationID>
                <PublicationTypeList>
                  <PublicationType>Preprint</PublicationType>
                </PublicationTypeList>
              </Article>
              <MedlineJournalInfo>
                <MedlineTA>Medline Test Journal</MedlineTA>
                <ISSNLinking>20493630</ISSNLinking>
              </MedlineJournalInfo>
            </MedlineCitation>
            <PubmedData>
              <ArticleIdList>
                <ArticleId IdType="doi">not a DOI</ArticleId>
              </ArticleIdList>
            </PubmedData>
          </PubmedArticle>
        </PubmedArticleSet>
    """

    # When: the complete response crosses the XML parsing boundary.
    records = parse_pubmed_article_set(payload)

    # Then: article and nested-field order is retained with normalized metadata.
    assert records == (
        PubMedRecord(
            pmid="111",
            doi="10.5555/preferred",
            title="Alpha beta: 2 findings.",
            authors=("Ada Lovelace", "The Consortium"),
            abstract="First section. Second section.",
            journal="The Journal of Tests",
            issns=("0028-0836", "2434-561X"),
            is_preprint=True,
        ),
        PubMedRecord(
            pmid="222",
            doi="10.7777/fallback",
            title="Fallback article",
            authors=(),
            abstract="",
            journal="Medline Test Journal",
            issns=("2049-3630",),
            is_preprint=False,
        ),
    )


def test_parse_pubmed_article_set_omits_invalid_doi_and_issns() -> None:
    # Given: an otherwise valid article containing only invalid identifiers.
    payload = b"""
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>333</PMID>
              <Article>
                <Journal><ISSN>0028-0837</ISSN></Journal>
                <ArticleTitle>Valid title</ArticleTitle>
                <ELocationID EIdType="doi" ValidYN="Y">invalid DOI</ELocationID>
              </Article>
              <MedlineJournalInfo><ISSNLinking>bad</ISSNLinking></MedlineJournalInfo>
            </MedlineCitation>
            <PubmedData><ArticleIdList><ArticleId IdType="doi">also invalid</ArticleId></ArticleIdList></PubmedData>
          </PubmedArticle>
        </PubmedArticleSet>
    """

    # When: identifiers are parsed through the shared normalizers.
    record = parse_pubmed_article_set(payload)[0]

    # Then: invalid optional identifiers are omitted rather than rejecting the article.
    assert (record.doi, record.issns, record.is_preprint) == (None, (), None)


@pytest.mark.parametrize(
    ("payload", "error_type", "expected_message"),
    [
        pytest.param(
            f"<PubmedArticleSet>{PRIVATE_DOI}".encode(),
            PubMedInvalidXmlError,
            "PubMed returned invalid XML",
            id="malformed-xml",
        ),
        pytest.param(
            f"""
                <PubmedArticleSet><PubmedArticle><MedlineCitation>
                  <Article><ArticleTitle>Private title</ArticleTitle></Article>
                </MedlineCitation><PubmedData><ArticleIdList>
                  <ArticleId IdType="doi">{PRIVATE_DOI}</ArticleId>
                </ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>
            """.encode(),
            PubMedMissingPmidError,
            "PubMed article is missing a PMID",
            id="missing-pmid",
        ),
        pytest.param(
            f"""
                <PubmedArticleSet><PubmedArticle><MedlineCitation>
                  <PMID>{PRIVATE_PMID}</PMID><Article><ArticleTitle>  </ArticleTitle></Article>
                </MedlineCitation></PubmedArticle></PubmedArticleSet>
            """.encode(),
            PubMedMissingTitleError,
            "PubMed article is missing a title",
            id="blank-title",
        ),
    ],
)
def test_parse_pubmed_article_set_rejects_invalid_required_input_with_redacted_errors(
    payload: bytes,
    error_type: type[PubMedMetadataError],
    expected_message: str,
) -> None:
    # Given: unsafe PubMed input that cannot form a valid domain record.
    # When: strict parsing rejects the response.
    with pytest.raises(error_type) as caught:
        _ = parse_pubmed_article_set(payload)

    # Then: the typed error exposes only static text, never XML or identifiers.
    observable = f"{caught.value!s}\n{caught.value!r}"
    assert str(caught.value) == expected_message
    assert PRIVATE_PMID not in observable
    assert PRIVATE_DOI not in observable
