"""Contracts for PubmedBookArticle metadata and mixed PubMed responses."""

import pytest

from zotero_arxiv_daily.retriever.pubmed_metadata import (
    PubMedMetadataError,
    PubMedMissingPmidError,
    PubMedMissingTitleError,
    PubMedRecord,
    parse_pubmed_article_set,
)


def test_mixed_article_set_preserves_order_and_maps_book_metadata() -> None:
    # Given: article, book, and article records in one document-order response.
    payload = b"""
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation><PMID>101</PMID><Article>
              <ArticleTitle>First article</ArticleTitle>
              <PublicationTypeList>
                <PublicationType UI="D016428">Journal Article</PublicationType>
              </PublicationTypeList>
            </Article></MedlineCitation>
          </PubmedArticle>
          <PubmedBookArticle>
            <BookDocument>
              <PMID>202</PMID>
              <ArticleIdList>
                <ArticleId IdType="doi">HTTPS://DOI.ORG/10.5555/Book-Primary</ArticleId>
              </ArticleIdList>
              <Book>
                <BookTitle>Testing <i>Handbook</i></BookTitle>
                <AuthorList>
                  <Author><CollectiveName>Fallback Editors</CollectiveName></Author>
                </AuthorList>
                <ELocationID EIdType="doi" ValidYN="Y">10.5555/book-fallback</ELocationID>
              </Book>
              <ArticleTitle>A <i>book</i> chapter</ArticleTitle>
              <AuthorList>
                <Author>
                  <ForeName>Grace</ForeName><LastName>Hopper</LastName><Suffix>Jr.</Suffix>
                </Author>
                <Author><CollectiveName>Book <b>Group</b></CollectiveName></Author>
              </AuthorList>
              <PublicationType UI="D000076942">Working Paper</PublicationType>
              <Abstract>
                <AbstractText>First <b>part</b>.</AbstractText>
                <AbstractText>Second part.</AbstractText>
              </Abstract>
            </BookDocument>
          </PubmedBookArticle>
          <PubmedArticle>
            <MedlineCitation><PMID>303</PMID><Article>
              <ArticleTitle>Last article</ArticleTitle>
            </Article></MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>
    """

    # When: the mixed response crosses the metadata boundary.
    records = parse_pubmed_article_set(payload)

    # Then: every supported child stays ordered and the book uses book paths.
    assert tuple(record.pmid for record in records) == ("101", "202", "303")
    assert records[1] == PubMedRecord(
        pmid="202",
        doi="10.5555/book-primary",
        title="A book chapter",
        authors=("Grace Hopper Jr.", "Book Group"),
        abstract="First part. Second part.",
        journal="Testing Handbook",
        issns=(),
        is_preprint=True,
    )
    assert (records[0].is_preprint, records[2].is_preprint) == (False, None)


def test_book_authors_fall_back_only_when_document_author_list_is_absent() -> None:
    # Given: one book without a document list and one with an empty document list.
    payload = b"""
        <PubmedArticleSet>
          <PubmedBookArticle><BookDocument>
            <PMID>401</PMID>
            <Book>
              <BookTitle>Fallback Book</BookTitle>
              <AuthorList><Author>
                <ForeName>Katherine</ForeName><LastName>Johnson</LastName>
              </Author></AuthorList>
            </Book>
            <ArticleTitle>Fallback chapter</ArticleTitle>
          </BookDocument></PubmedBookArticle>
          <PubmedBookArticle><BookDocument>
            <PMID>402</PMID>
            <Book>
              <BookTitle>Primary Book</BookTitle>
              <AuthorList><Author>
                <ForeName>Ignored</ForeName><LastName>Author</LastName>
              </Author></AuthorList>
            </Book>
            <ArticleTitle>Empty primary chapter</ArticleTitle>
            <AuthorList />
          </BookDocument></PubmedBookArticle>
        </PubmedArticleSet>
    """

    # When: both author-list shapes are parsed.
    records = parse_pubmed_article_set(payload)

    # Then: fallback occurs for absence, not for a present empty primary list.
    assert tuple(record.authors for record in records) == (
        ("Katherine Johnson",),
        (),
    )


def test_book_uses_valid_elocation_doi_and_does_not_classify_text_alone() -> None:
    # Given: invalid preferred IDs, one rejected DOI, and Preprint text without a UI.
    payload = b"""
        <PubmedArticleSet><PubmedBookArticle><BookDocument>
          <PMID>501</PMID>
          <ArticleIdList>
            <ArticleId IdType="doi">not a DOI</ArticleId>
          </ArticleIdList>
          <Book>
            <BookTitle>DOI Fallback Book</BookTitle>
            <ELocationID EIdType="doi" ValidYN="N">10.5555/rejected</ELocationID>
            <ELocationID EIdType="doi" ValidYN="Y">doi.org/10.5555/Book-Fallback</ELocationID>
          </Book>
          <ArticleTitle>DOI fallback chapter</ArticleTitle>
          <PublicationType>Preprint</PublicationType>
        </BookDocument></PubmedBookArticle></PubmedArticleSet>
    """

    # When: identifiers and publication types are parsed from exact book paths.
    record = parse_pubmed_article_set(payload)[0]

    # Then: the valid fallback DOI is normalized and text alone classifies false.
    assert (record.doi, record.is_preprint) == ("10.5555/book-fallback", False)


def test_whole_book_records_title_from_book_title_when_article_title_unusable() -> None:
    # Given: whole-book records with absent and with blank ArticleTitle.
    payload = b"""
        <PubmedArticleSet>
          <PubmedBookArticle><BookDocument>
            <PMID>701</PMID>
            <ArticleIdList>
              <ArticleId IdType="doi">10.5555/Whole-Book</ArticleId>
            </ArticleIdList>
            <Book>
              <BookTitle>Whole <i>Book</i> Absent</BookTitle>
              <AuthorList><Author>
                <ForeName>Ada</ForeName><LastName>Lovelace</LastName>
              </Author></AuthorList>
            </Book>
            <Abstract><AbstractText>Whole book abstract.</AbstractText></Abstract>
            <PublicationType UI="D000076942">Preprint</PublicationType>
          </BookDocument></PubmedBookArticle>
          <PubmedBookArticle><BookDocument>
            <PMID>702</PMID>
            <Book><BookTitle>Whole Book Blank</BookTitle></Book>
            <ArticleTitle> </ArticleTitle>
          </BookDocument></PubmedBookArticle>
        </PubmedArticleSet>
    """

    # When: both whole-book shapes cross the metadata boundary.
    records = parse_pubmed_article_set(payload)

    # Then: BookTitle supplies the title and other book fields stay unchanged.
    assert records[0] == PubMedRecord(
        pmid="701",
        doi="10.5555/whole-book",
        title="Whole Book Absent",
        authors=("Ada Lovelace",),
        abstract="Whole book abstract.",
        journal="Whole Book Absent",
        issns=(),
        is_preprint=True,
    )
    assert (records[1].pmid, records[1].title) == ("702", "Whole Book Blank")


def test_book_article_title_takes_precedence_over_book_title() -> None:
    # Given: a book record with both a nonblank ArticleTitle and a BookTitle.
    payload = b"""
        <PubmedArticleSet><PubmedBookArticle><BookDocument>
          <PMID>703</PMID>
          <Book><BookTitle>Container Book</BookTitle></Book>
          <ArticleTitle>Chapter wins</ArticleTitle>
        </BookDocument></PubmedBookArticle></PubmedArticleSet>
    """

    # When: the chapter-level record is parsed.
    record = parse_pubmed_article_set(payload)[0]

    # Then: ArticleTitle remains preferred and BookTitle stays the journal.
    assert (record.title, record.journal) == ("Chapter wins", "Container Book")


@pytest.mark.parametrize(
    ("book_document", "error_type", "expected_message"),
    [
        pytest.param(
            b"<BookDocument><ArticleTitle>Missing PMID</ArticleTitle></BookDocument>",
            PubMedMissingPmidError,
            "PubMed article is missing a PMID",
            id="missing-pmid",
        ),
        pytest.param(
            b"<BookDocument><PMID>601</PMID><ArticleTitle> </ArticleTitle></BookDocument>",
            PubMedMissingTitleError,
            "PubMed article is missing a title",
            id="blank-article-title-without-book",
        ),
        pytest.param(
            b"<BookDocument><PMID>602</PMID><Book><BookTitle> </BookTitle></Book><ArticleTitle> </ArticleTitle></BookDocument>",
            PubMedMissingTitleError,
            "PubMed article is missing a title",
            id="both-titles-blank",
        ),
        pytest.param(
            b"<BookDocument><PMID>603</PMID><Book /></BookDocument>",
            PubMedMissingTitleError,
            "PubMed article is missing a title",
            id="both-titles-absent",
        ),
    ],
)
def test_book_rejects_missing_required_metadata(
    book_document: bytes,
    error_type: type[PubMedMetadataError],
    expected_message: str,
) -> None:
    # Given: a book record missing one required identity field.
    payload = b"<PubmedArticleSet><PubmedBookArticle>" + book_document + (
        b"</PubmedBookArticle></PubmedArticleSet>"
    )

    # When / Then: the shared static metadata errors reject the record.
    with pytest.raises(error_type) as caught:
        _ = parse_pubmed_article_set(payload)
    assert str(caught.value) == expected_message
