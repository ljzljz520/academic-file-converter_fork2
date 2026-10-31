import calendar
import os
import re
from datetime import datetime
from pathlib import Path

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.customization import convert_to_unicode, latex_to_unicode

from academic.generate_markdown import GenerateMarkdown
from academic.publication_type import PUB_TYPES_BIBTEX_TO_CSL

# Private record key used to carry name structure parsed before `convert_to_unicode`
# strips the protective braces from the BibTeX entry.
_NAMES_KEY = "_academic_names"


def import_bibtex(
    bibtex,
    pub_dir=os.path.join("content", "publication"),
    featured=False,
    overwrite=False,
    normalize=False,
    compact=False,
    dry_run=False,
):
    """Import publications from BibTeX file"""
    from academic.cli import log
    from academic.utils import AcademicError

    # Check BibTeX file exists.
    if not Path(bibtex).is_file():
        err = "Please check the path to your BibTeX file and re-run"
        log.error(err)
        raise AcademicError(err)

    # Load BibTeX file for parsing.
    with open(bibtex, "r", encoding="utf-8") as bibtex_file:
        parser = BibTexParser(common_strings=True)
        parser.customization = academic_customization
        parser.ignore_nonstandard_types = False
        bib_database = bibtexparser.load(bibtex_file, parser=parser)
        for entry in bib_database.entries:
            parse_bibtex_entry(
                entry,
                pub_dir=pub_dir,
                featured=featured,
                overwrite=overwrite,
                normalize=normalize,
                compact=compact,
                dry_run=dry_run,
            )


def parse_bibtex_entry(
    entry,
    pub_dir=os.path.join("content", "publication"),
    featured=False,
    overwrite=False,
    normalize=False,
    compact=False,
    dry_run=False,
):
    """Parse a bibtex entry and generate corresponding publication bundle"""
    from academic.cli import log

    log.info(f"Parsing entry {entry['ID']}")

    # Names are parsed before the unicode conversion strips protective braces;
    # detach the private parse result so it never leaks into `cite.bib`.
    name_info = entry.pop(_NAMES_KEY, None)

    bundle_path = os.path.join(pub_dir, slugify(entry["ID"]))
    markdown_path = os.path.join(bundle_path, "index.md")
    cite_path = os.path.join(bundle_path, "cite.bib")
    date = datetime.utcnow()
    timestamp = date.isoformat("T") + "Z"  # RFC 3339 timestamp.

    # Do not overwrite publication bundle if it already exists.
    if not overwrite and os.path.isdir(bundle_path):
        log.warning(f"Skipping creation of {bundle_path} as it already exists. " f"To overwrite, add the `--overwrite` argument.")
        return

    # Create bundle dir.
    log.info(f"Creating folder {bundle_path}")
    if not dry_run:
        Path(bundle_path).mkdir(parents=True, exist_ok=True)

    # Save citation file. Restore the original author/editor strings while writing
    # so that corporate brace groups and personal name semantics are preserved.
    log.info(f"Saving citation to {cite_path}")
    saved_name_fields = {}
    if name_info is not None:
        for field, raw_value in name_info.get("raw", {}).items():
            saved_name_fields[field] = entry.get(field)
            entry[field] = raw_value
    db = BibDatabase()
    db.entries = [entry]
    writer = BibTexWriter()
    if not dry_run:
        with open(cite_path, "w", encoding="utf-8") as f:
            f.write(writer.write(db))
    for field, value in saved_name_fields.items():
        if value is None:
            entry.pop(field, None)
        else:
            entry[field] = value

    # Prepare YAML front matter for Markdown file.
    if not dry_run:
        from importlib import resources as import_resources

        # Load the Markdown template from within the `templates` folder of the `academic` package
        template = import_resources.read_text(__package__ + ".templates", "publication.md")

        with open(markdown_path, "w") as f:
            f.write(template)

    page = GenerateMarkdown(Path(bundle_path), dry_run=dry_run, compact=compact)
    page.load(Path("index.md"))

    page.yaml["title"] = clean_bibtex_str(entry["title"])

    if "subtitle" in entry:
        page.yaml["subtitle"] = clean_bibtex_str(entry["subtitle"])

    year, month, day = "", "01", "01"
    if "date" in entry:
        date_parts = entry["date"].split("-")
        if len(date_parts) == 3:
            year, month, day = date_parts[0], date_parts[1], date_parts[2]
        elif len(date_parts) == 2:
            year, month = date_parts[0], date_parts[1]
        elif len(date_parts) == 1:
            year = date_parts[0]
    if "month" in entry and month == "01":
        month = month2number(entry["month"])
    if "year" in entry and year == "":
        year = entry["year"]
    if len(year) == 0:
        log.error(f'Invalid date for entry `{entry["ID"]}`.')

    page.yaml["date"] = f"{year}-{month}-{day}"
    page.yaml["publishDate"] = timestamp

    # Prefer the names parsed while protective braces were still present;
    # fall back to parsing the (possibly brace-less) entry for callers that did
    # not use `academic_customization`. Missing authors fall back to editors.
    authors = name_info.get("authors") if name_info is not None else None
    if not authors:
        for field in ("author", "editor"):
            raw_names = entry.get(field)
            if isinstance(raw_names, str) and raw_names.strip():
                authors = parse_bibtex_authors(raw_names)
                break

    if authors:
        page.yaml["authors"] = authors

    # Convert Bibtex publication type to the universal CSL standard, defaulting to `manuscript`
    default_csl_type = "manuscript"
    pub_type = PUB_TYPES_BIBTEX_TO_CSL.get(entry["ENTRYTYPE"], default_csl_type)
    page.yaml["publication_types"] = [pub_type]

    if "abstract" in entry:
        page.yaml["abstract"] = clean_bibtex_str(entry["abstract"])
    else:
        page.yaml["abstract"] = ""

    page.yaml["featured"] = featured

    # Publication name.
    # This field is Markdown formatted, wrapping the publication name in `*` for italics
    if "booktitle" in entry:
        publication = "*" + clean_bibtex_str(entry["booktitle"]) + "*"
    elif "journal" in entry:
        publication = "*" + clean_bibtex_str(entry["journal"]) + "*"
    elif "publisher" in entry:
        publication = "*" + clean_bibtex_str(entry["publisher"]) + "*"
    else:
        publication = ""
    page.yaml["publication"] = publication

    if "keywords" in entry:
        page.yaml["tags"] = clean_bibtex_tags(entry["keywords"], normalize)

    if "doi" in entry:
        page.yaml["doi"] = clean_bibtex_str(entry["doi"])

    links = []
    if all(f in entry for f in ["archiveprefix", "eprint"]) and entry["archiveprefix"].lower() == "arxiv":
        links += [{"name": "arXiv", "url": "https://arxiv.org/abs/" + clean_bibtex_str(entry["eprint"])}]

    if "url" in entry:
        sane_url = clean_bibtex_str(entry["url"])

        if sane_url[-4:].lower() == ".pdf":
            page.yaml["url_pdf"] = sane_url
        else:
            links += [{"name": "URL", "url": sane_url}]

    if links:
        page.yaml["links"] = links

    # Save Markdown file.
    try:
        log.info(f"Saving Markdown to '{markdown_path}'")
        if not dry_run:
            page.dump()
    except IOError:
        log.error("Could not save file.")
    return page


def slugify(s, lower=True):
    bad_symbols = (".", "_", ":")  # Symbols to replace with hyphen delimiter.
    delimiter = "-"
    good_symbols = (delimiter,)  # Symbols to keep.
    for r in bad_symbols:
        s = s.replace(r, delimiter)

    s = re.sub(r"(\D+)(\d+)", r"\1\-\2", s)  # Delimit non-number, number.
    s = re.sub(r"(\d+)(\D+)", r"\1\-\2", s)  # Delimit number, non-number.
    s = re.sub(r"((?<=[a-z])[A-Z]|(?<!\A)[A-Z](?=[a-z]))", r"\-\1", s)  # Delimit camelcase.
    s = "".join(c for c in s if c.isalnum() or c in good_symbols).strip()  # Strip non-alphanumeric and non-hyphen.
    s = re.sub("-{2,}", "-", s)  # Remove consecutive hyphens.

    if lower:
        s = s.lower()
    return s


def academic_customization(record):
    """BibTexParser customization.

    Author and editor name structure is resolved BEFORE `convert_to_unicode`
    strips the protective braces (which would merge corporate authors into
    personal names). The parsed display names and the raw field values are
    stashed privately; the raw values restore brace grouping in `cite.bib`.
    """
    raw_name_fields = {}
    for field in ("author", "editor"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            raw_name_fields[field] = value

    parsed_authors = None
    if "author" in raw_name_fields:
        parsed_authors = parse_bibtex_authors(raw_name_fields["author"])
    elif "editor" in raw_name_fields:
        parsed_authors = parse_bibtex_authors(raw_name_fields["editor"])

    record = convert_to_unicode(record)

    if parsed_authors is not None:
        record[_NAMES_KEY] = {"authors": parsed_authors, "raw": raw_name_fields}
    return record


def parse_bibtex_authors(author_field):
    """Parse a raw BibTeX author/editor field into order-preserving display names.

    The field is split on top-level `` and `` separators only, so brace-protected
    corporate authors containing ``and`` or commas (e.g.
    ``{University of Somewhere, Department of Something}``) remain a single
    author. Personal names support the BibTeX ``First von Last``,
    ``von Last, First`` and ``von Last, Jr, First`` forms, keeping ``von``
    particles attached to the last name and placing suffixes after it.
    """
    authors = []
    for raw_name in _split_authors(author_field):
        display_name = parse_bibtex_name(raw_name)
        if display_name:
            authors.append(display_name)
    return authors


def clean_bibtex_authors(author_str):
    """Convert a list of pre-split author names to `firstname(s) lastname` format."""
    authors = []
    for raw_name in author_str:
        display_name = parse_bibtex_name(raw_name)
        if display_name:
            authors.append(display_name)
    return authors


def parse_bibtex_name(raw_name):
    """Parse a single BibTeX name, either a brace-protected group or a personal name."""
    name = raw_name.strip()
    if not name:
        return ""

    # A name wrapped in a single top-level brace group is a literal (corporate) name.
    inner, is_group = _unwrap_brace_group(name)
    if is_group:
        return _clean_name(inner)

    return _parse_personal_name(name)


def _split_authors(field):
    """Split an author field on whitespace-delimited `and` at brace depth zero."""
    parts = []
    start = 0
    for match in re.finditer(r"\s+and\s+", field):
        # Recompute the depth from the last accepted cut so that separators
        # nested inside brace groups (e.g. `Sound and Vibration`) do not confuse it.
        match_start = match.start()
        segment = field[start:match_start]
        if segment.count("{") - segment.count("}") == 0:
            parts.append(segment)
            start = match.end()
    parts.append(field[start:])
    return [part.strip() for part in parts if part.strip()]


def _unwrap_brace_group(text):
    """Return the inner text if `text` is a single balanced brace group."""
    text = text.strip()
    if not text.startswith("{"):
        return text, False
    depth = 0
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                if index == len(text) - 1:
                    return text[1:-1].strip(), True
                break
    return text, False


def _split_top_level_commas(text):
    """Split on commas that are not nested inside brace groups."""
    parts = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return [part.strip() for part in parts]


def _token_starts_lower(token):
    """Apply the BibTeX von-particle rule to a token.

    The first character outside brace groups decides case; a fully braced
    token counts as lower-case (the standard trick to protect a literal part).
    Non-letters follow BibTeX and are treated as lower-case.
    """
    depth = 0
    for char in token:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif depth == 0:
            return not char.isupper()
    return True


def _split_first_von_last(tokens):
    """Split `First von Last` tokens into (first, von, last) following BibTeX."""
    if len(tokens) == 1:
        return [], [], list(tokens)

    lower_indexes = [i for i, token in enumerate(tokens) if _token_starts_lower(token)]
    if not lower_indexes:
        return list(tokens[:-1]), [], [tokens[-1]]

    first_lower = lower_indexes[0]
    # The von run must leave at least one token for the last name.
    von_indexes = [i for i in lower_indexes if i < len(tokens) - 1]
    if not von_indexes:
        return list(tokens[:-1]), [], [tokens[-1]]

    last_von = max(von_indexes)
    von_end = last_von + 1
    return list(tokens[:first_lower]), list(tokens[first_lower:von_end]), list(tokens[von_end:])


def _split_von_last(tokens):
    """Split `von Last` tokens (comma form) into (von, last) following BibTeX."""
    lower_indexes = [i for i, token in enumerate(tokens) if _token_starts_lower(token)]
    if not lower_indexes:
        return [], list(tokens)

    first_lower = lower_indexes[0]
    von_indexes = [i for i in lower_indexes if i < len(tokens) - 1]
    if not von_indexes:
        return [], list(tokens)

    last_von = max(von_indexes)
    von_end = last_von + 1
    von = list(tokens[first_lower:von_end])
    last = list(tokens[:first_lower]) + list(tokens[von_end:])
    return von, last


# Generational name suffixes as written without commas, e.g. `John Smith Jr`.
_NAME_SUFFIXES = {"jr", "jnr", "junior", "sr", "snr", "senior"}


def _is_name_suffix(token):
    return _clean_name(token).strip(".,;:").lower() in _NAME_SUFFIXES


def _parse_personal_name(raw_name):
    """Parse a personal BibTeX name into `firstname(s) [von] lastname [suffix]`."""
    parts = _split_top_level_commas(raw_name)
    suffix = ""

    if len(parts) == 1:
        first, von, last = _split_first_von_last(parts[0].split())
    else:
        # Comma form: `von Last, [Jr, ] First`.
        von, last = _split_von_last(parts[0].split())
        first = parts[-1].split()
        if len(parts) > 2:
            suffix = ", ".join(part for part in parts[1:-1] if part)

    # Tolerate suffixes written without commas (e.g. `John Smith Jr`).
    if not suffix and last and _is_name_suffix(last[-1]) and (first or von or len(last) > 1):
        suffix = last.pop()

    cleaned_tokens = [_clean_name(token) for token in first + von + last]
    name = " ".join(token for token in cleaned_tokens if token)
    if suffix:
        suffix = _clean_name(suffix)
        if suffix:
            name = f"{name} {suffix}" if name else suffix
    return name


def _clean_name(text):
    """Convert LaTeX accents to unicode, drop protective braces and collapse spaces."""
    return re.sub(r"\s+", " ", latex_to_unicode(text)).strip()


def clean_bibtex_str(s):
    """Clean BibTeX string and escape TOML special characters"""
    s = s.replace("\\", "")
    s = s.replace('"', '\\"')
    s = s.replace("{", "").replace("}", "")
    s = s.replace("\t", " ").replace("\n", " ").replace("\r", "")
    return s


def clean_bibtex_tags(s, normalize=False):
    """Clean BibTeX keywords and convert to TOML tags"""

    tags = clean_bibtex_str(s).split(",")
    tags = [tag.strip() for tag in tags]

    if normalize:
        tags = [tag.lower().capitalize() for tag in tags]

    return tags


def month2number(month):
    """Convert BibTeX or BibLateX month to numeric"""
    from academic.cli import log

    if len(month) <= 2:  # Assume a 1 or 2 digit numeric month has been given.
        return month.zfill(2)
    else:  # Assume a textual month has been given.
        month_abbr = month.strip()[:3].title()
        try:
            return str(list(calendar.month_abbr).index(month_abbr)).zfill(2)
        except ValueError:
            log.error("Please update the entry with a valid month.")
