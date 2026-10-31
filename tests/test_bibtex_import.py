import typing
from pathlib import Path

import bibtexparser
from bibtexparser.bparser import BibTexParser

from academic import cli, import_bibtex
from academic.generate_markdown import GenerateMarkdown

bibtex_dir = Path(__file__).parent / "data"


def test_bibtex_import():
    cli.parse_args(["import", "--dry-run", "tests/data/article.bib", "content/publication/"])


def _process_bibtex(file, expected_count=1) -> "typing.List[GenerateMarkdown]":
    """
    Parse a BibTeX .bib file and return the parsed metadata
    :param file: The .bib file to parse
    :param expected_count: The expected number of entries inside the .bib
    :return: The parsed metadata as a list of EditableFM
    """
    parser = BibTexParser(common_strings=True)
    parser.customization = import_bibtex.academic_customization
    parser.ignore_nonstandard_types = False
    with Path(bibtex_dir, file).open("r", encoding="utf-8") as bibtex_file:
        bib_database = bibtexparser.load(bibtex_file, parser=parser)
        results = []
        for entry in bib_database.entries:
            results.append(import_bibtex.parse_bibtex_entry(entry, dry_run=True))
        assert len(results) == expected_count
        return results


def _test_publication_type(metadata: GenerateMarkdown, expected_type: str):
    """
    Check that the publication_types field of the parsed metadata is set to the expected type.
    """
    assert metadata.yaml["publication_types"] == [expected_type]


def test_bibtex_types():
    """
    This test uses the import_bibtex functions to parse a .bib file and checks that the
    resulting metadata has the correct publication type set.
    """
    _test_publication_type(_process_bibtex("article.bib")[0], "article-journal")
    for metadata in _process_bibtex("report.bib", expected_count=2):
        _test_publication_type(metadata, "report")
    for metadata in _process_bibtex("thesis.bib", expected_count=3):
        _test_publication_type(metadata, "thesis")
    for metadata in _process_bibtex("book.bib", expected_count=2):
        _test_publication_type(metadata, "book")


def test_parse_bibtex_authors():
    """Names are split on top-level `and` only, preserving order and brace groups."""
    # Corporate authors keep their internal commas and `and`, personal names keep order.
    assert import_bibtex.parse_bibtex_authors("{University of Somewhere, Department of Something} and Nelson Bigetti") == [
        "University of Somewhere, Department of Something",
        "Nelson Bigetti",
    ]
    assert import_bibtex.parse_bibtex_authors("{International Association of Sound and Vibration}") == [
        "International Association of Sound and Vibration"
    ]
    # Nested protective groups are unwrapped for display.
    assert import_bibtex.parse_bibtex_authors("{{Nested Org, Inc}}") == ["Nested Org, Inc"]


def test_parse_personal_names():
    """Personal names resolve First/von/Last/suffix parts in display order."""
    # Simple `First Last` and `Last, First` forms.
    assert import_bibtex.parse_bibtex_authors("Nelson Bigetti") == ["Nelson Bigetti"]
    assert import_bibtex.parse_bibtex_authors("Bigetti, Nelson") == ["Nelson Bigetti"]
    # Suffix form `Last, Jr, First` keeps the suffix after the last name.
    assert import_bibtex.parse_bibtex_authors("Smith, Jr., John") == ["John Smith Jr."]
    assert import_bibtex.parse_bibtex_authors("John Smith Jr") == ["John Smith Jr"]
    # Initials and von particles stay attached to the last name, in order.
    assert import_bibtex.parse_bibtex_authors("Kennedy, John F.") == ["John F. Kennedy"]
    assert import_bibtex.parse_bibtex_authors("van der Sar, Edwin") == ["Edwin van der Sar"]
    assert import_bibtex.parse_bibtex_authors("da Vinci, Leonardo") == ["Leonardo da Vinci"]
    assert import_bibtex.parse_bibtex_authors("Ludwig van Beethoven") == ["Ludwig van Beethoven"]
    assert import_bibtex.parse_bibtex_authors("Charles Louis Joseph de la Vallee Poussin") == ["Charles Louis Joseph de la Vallee Poussin"]
    # LaTeX accents are converted without disturbing the name structure.
    assert import_bibtex.parse_bibtex_authors('M{\\"u}ller, Hans') == ["Hans Müller"]


def test_bibtex_authors_metadata():
    """End-to-end parsing of the author fixture into ordered YAML author lists."""
    results = {page.yaml["title"]: page.yaml for page in _process_bibtex("authors.bib", expected_count=6)}

    assert results["Simple name"]["authors"] == ["Nelson Bigetti"]

    assert results["Personal name forms"]["authors"] == [
        "Nelson Bigetti",
        "John Smith Jr.",
        "John F. Kennedy",
        "Edwin van der Sar",
        "Leonardo da Vinci",
        "Ludwig van Beethoven",
    ]

    # Corporate authors are not split on their internal comma or `and`, and order is preserved.
    assert results["Corporate and mixed authors"]["authors"] == [
        "University of Somewhere, Department of Something",
        "International Association of Sound and Vibration",
        "Nelson Bigetti",
    ]

    assert results["Accented names"]["authors"] == ["Hans Müller", "Ëlise Müller"]

    # Editors are used when authors are absent; an entry with neither has no authors key.
    assert results["Editor fallback"]["authors"] == ["Jane Doe", "Institution Press"]
    assert "authors" not in results["No people at all"]


def test_cite_bib_preserves_author_groups(tmp_path):
    """The raw author/editor grouping survives in the written cite.bib file."""
    parser = BibTexParser(common_strings=True)
    parser.customization = import_bibtex.academic_customization
    parser.ignore_nonstandard_types = False
    with Path(bibtex_dir, "authors.bib").open("r", encoding="utf-8") as bibtex_file:
        bib_database = bibtexparser.load(bibtex_file, parser=parser)

    entry = next(entry for entry in bib_database.entries if entry["ID"] == "corporate")
    import_bibtex.parse_bibtex_entry(entry, pub_dir=str(tmp_path), dry_run=False)

    cite_bib = (tmp_path / "corporate" / "cite.bib").read_text(encoding="utf-8")
    assert "{University of Somewhere, Department of Something}" in cite_bib
    assert "{International Association of Sound and Vibration}" in cite_bib
    # The private parse metadata must never be serialized into cite.bib.
    assert import_bibtex._NAMES_KEY not in cite_bib
