#!/usr/bin/env python3
"""Collect weekly AI + health papers from free public sources.

Sources used by this script:
- RSS feeds
- PubMed E-utilities API
- arXiv API

The script does not use OpenAI APIs or paid services.  It only uses the Python
standard library so beginners can run it without managing many dependencies.
"""

import argparse
import datetime as dt
import email.utils
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = "weekly-health-ai-papers/1.0 (+https://github.com/)"
NO_ABSTRACT = "No abstract available."


class SimpleHttpError(Exception):
    """Raised when a public API or RSS request fails."""


class SimpleYamlError(Exception):
    """Raised when the small built-in YAML reader cannot parse config.yaml."""


def strip_quotes(value: str) -> str:
    """Remove matching single or double quotes from a YAML scalar."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_scalar(value: str) -> Any:
    """Parse the small set of scalar values used in config.yaml."""
    value = strip_quotes(value.strip())
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.isdigit():
        return int(value)
    return value


def remove_yaml_comment(line: str) -> str:
    """Remove comments while keeping # characters that appear inside quotes."""
    in_single = False
    in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:index]
    return line


def load_config(path: Path) -> dict[str, Any]:
    """Load this project's beginner-friendly YAML configuration.

    This small reader supports the simple YAML style used in config.yaml:
    dictionaries, lists, strings, integers, booleans, comments, and indentation.
    If you later need advanced YAML features, replace this function with
    PyYAML's yaml.safe_load and add PyYAML to requirements.txt.
    """
    rows: list[tuple[int, str, int]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        without_comment = remove_yaml_comment(raw_line).rstrip()
        if not without_comment.strip():
            continue
        indent = len(without_comment) - len(without_comment.lstrip(" "))
        rows.append((indent, without_comment.strip(), line_number))

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(rows):
            return {}, index

        first_indent, first_text, _ = rows[index]
        if first_indent < indent:
            return {}, index

        if first_text.startswith("- "):
            items: list[Any] = []
            while index < len(rows):
                current_indent, text, line_number = rows[index]
                if current_indent < indent:
                    break
                if current_indent > indent:
                    raise SimpleYamlError(f"Line {line_number}: unexpected indentation")
                if not text.startswith("- "):
                    break

                item_text = text[2:].strip()
                index += 1

                if not item_text:
                    value, index = parse_block(index, indent + 2)
                    items.append(value)
                elif ":" in item_text:
                    key, raw_value = item_text.split(":", 1)
                    item: dict[str, Any] = {}
                    if raw_value.strip():
                        item[key.strip()] = parse_scalar(raw_value)
                    else:
                        value, index = parse_block(index, indent + 2)
                        item[key.strip()] = value

                    while index < len(rows):
                        next_indent, next_text, next_line = rows[index]
                        if next_indent <= indent:
                            break
                        if next_indent != indent + 2:
                            raise SimpleYamlError(f"Line {next_line}: unexpected indentation")
                        if next_text.startswith("- "):
                            break
                        if ":" not in next_text:
                            raise SimpleYamlError(f"Line {next_line}: expected key: value")
                        nested_key, nested_raw_value = next_text.split(":", 1)
                        index += 1
                        if nested_raw_value.strip():
                            item[nested_key.strip()] = parse_scalar(nested_raw_value)
                        else:
                            nested_value, index = parse_block(index, indent + 4)
                            item[nested_key.strip()] = nested_value
                    items.append(item)
                else:
                    items.append(parse_scalar(item_text))
            return items, index

        mapping: dict[str, Any] = {}
        while index < len(rows):
            current_indent, text, line_number = rows[index]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise SimpleYamlError(f"Line {line_number}: unexpected indentation")
            if text.startswith("- "):
                break
            if ":" not in text:
                raise SimpleYamlError(f"Line {line_number}: expected key: value")

            key, raw_value = text.split(":", 1)
            index += 1
            if raw_value.strip():
                mapping[key.strip()] = parse_scalar(raw_value)
            else:
                value, index = parse_block(index, indent + 2)
                mapping[key.strip()] = value
        return mapping, index

    parsed, next_index = parse_block(0, 0)
    if next_index != len(rows):
        _, _, line_number = rows[next_index]
        raise SimpleYamlError(f"Line {line_number}: could not parse the remaining configuration")
    if not isinstance(parsed, dict):
        raise SimpleYamlError("The root of config.yaml must be a dictionary")
    return parsed


def utc_today() -> dt.date:
    """Return today's date in UTC."""
    return dt.datetime.now(dt.timezone.utc).date()


def parse_date(value: str | None) -> dt.date | None:
    """Parse common date formats from RSS, PubMed, or arXiv."""
    if not value:
        return None
    value = value.strip()

    try:
        return email.utils.parsedate_to_datetime(value).date()
    except (TypeError, ValueError, IndexError):
        pass

    normalized = value.replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(normalized).date()
    except ValueError:
        pass

    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None

    return None


def clean_text(value: str | None) -> str:
    """Convert HTML-ish text into plain, compact text."""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def local_name(tag: str) -> str:
    """Return an XML tag name without its namespace."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def child_text(element: ET.Element | None, path: str) -> str:
    """Find a child by local-name path and return clean text."""
    if element is None:
        return ""
    current = element
    for name in path.split("/"):
        next_child = None
        for child in current:
            if local_name(child.tag) == name:
                next_child = child
                break
        if next_child is None:
            return ""
        current = next_child
    return clean_text("".join(current.itertext()))


def descendants(element: ET.Element, name: str) -> list[ET.Element]:
    """Find all descendants by local tag name."""
    return [child for child in element.iter() if local_name(child.tag) == name]


def http_get_text(url: str, params: dict[str, Any] | None = None, timeout: int = 30) -> str:
    """Download text from a URL using a friendly user agent."""
    full_url = f"{url}?{urlencode(params)}" if params else url
    request = Request(full_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except OSError as error:
        raise SimpleHttpError(str(error)) from error


def keyword_matches(title: str, abstract: str, keywords: list[str]) -> bool:
    """Return True if any configured keyword appears in title or abstract."""
    haystack = f"{title} {abstract}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def is_recent(published: dt.date | None, start_date: dt.date, end_date: dt.date) -> bool:
    """Return True when a publication date is inside the requested date window."""
    return published is not None and start_date <= published <= end_date


def normalize_key(title: str, link: str) -> str:
    """Build a simple key used to remove duplicate papers from multiple sources."""
    normalized_title = re.sub(r"\W+", " ", title.lower()).strip()
    normalized_link = link.strip().lower()
    return normalized_link or normalized_title


def collect_from_rss(
    feeds: list[dict[str, str]],
    keywords: list[str],
    start_date: dt.date,
    end_date: dt.date,
) -> list[dict[str, Any]]:
    """Collect matching papers from configured RSS or Atom feeds."""
    papers: list[dict[str, Any]] = []

    for feed in feeds:
        feed_name = feed.get("name", "RSS feed")
        feed_url = feed.get("url")
        if not feed_url:
            continue

        try:
            feed_text = http_get_text(feed_url)
            root = ET.fromstring(feed_text)
        except (SimpleHttpError, ET.ParseError) as error:
            print(f"Warning: RSS feed failed ({feed_name}): {error}", file=sys.stderr)
            continue

        root_name = local_name(root.tag).lower()
        entries = descendants(root, "entry") if root_name == "feed" else descendants(root, "item")

        for entry in entries:
            title = child_text(entry, "title")
            abstract = (
                child_text(entry, "summary")
                or child_text(entry, "description")
                or child_text(entry, "subtitle")
                or NO_ABSTRACT
            )
            link = child_text(entry, "link")
            if not link:
                for child in entry:
                    if local_name(child.tag) == "link" and child.attrib.get("href"):
                        link = child.attrib["href"]
                        break
            published = parse_date(
                child_text(entry, "pubDate")
                or child_text(entry, "published")
                or child_text(entry, "updated")
                or child_text(entry, "date")
            )

            if not title or not is_recent(published, start_date, end_date):
                continue
            if not keyword_matches(title, abstract, keywords):
                continue

            authors = child_text(entry, "author") or child_text(entry, "creator") or "Not listed"
            papers.append(
                {
                    "title": title,
                    "journal": feed_name,
                    "authors": authors,
                    "published": published,
                    "abstract": abstract or NO_ABSTRACT,
                    "link": link,
                    "source": "RSS",
                }
            )

    return papers


def build_pubmed_query(journals: list[str], keywords: list[str], start_date: dt.date, end_date: dt.date) -> str:
    """Create a PubMed query for journals, keywords, and publication dates."""
    journal_query = " OR ".join(f'"{journal}"[Journal]' for journal in journals)
    keyword_query = " OR ".join(f'"{keyword}"[Title/Abstract]' for keyword in keywords)
    date_query = f'("{start_date.isoformat()}"[Date - Publication] : "{end_date.isoformat()}"[Date - Publication])'
    return f"({journal_query}) AND ({keyword_query}) AND {date_query}"


def parse_pubmed_date(article: ET.Element) -> dt.date | None:
    """Extract the best available publication date from a PubMed article."""
    date_elements = descendants(article, "ArticleDate") or descendants(article, "PubDate")
    if not date_elements:
        return None
    date_element = date_elements[0]

    year = child_text(date_element, "Year")
    month = child_text(date_element, "Month") or "1"
    day = child_text(date_element, "Day") or "1"

    month_lookup = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    if month and not month.isdigit():
        month = str(month_lookup.get(month[:3].lower(), 1))

    try:
        return dt.date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def collect_from_pubmed(
    config: dict[str, Any],
    keywords: list[str],
    start_date: dt.date,
    end_date: dt.date,
) -> list[dict[str, Any]]:
    """Collect matching papers from PubMed E-utilities."""
    if not config.get("enabled", True):
        return []

    journals = config.get("journals", [])
    if not journals:
        return []

    query = build_pubmed_query(journals, keywords, start_date, end_date)
    common_params = {
        "tool": config.get("tool", "weekly-health-ai-papers"),
        "email": config.get("email", "your-email@example.com"),
    }

    search_text = http_get_text(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={
            **common_params,
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": int(config.get("max_results", 80)),
            "sort": "pub date",
        },
    )
    pmids = json.loads(search_text).get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []

    time.sleep(0.35)
    fetch_text = http_get_text(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params={
            **common_params,
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
        },
    )

    root = ET.fromstring(fetch_text)
    papers: list[dict[str, Any]] = []

    for article in descendants(root, "PubmedArticle"):
        medline = next(iter(descendants(article, "MedlineCitation")), None)
        article_element = next(iter(descendants(article, "Article")), None)
        pmid = child_text(medline, "PMID")
        title = child_text(article_element, "ArticleTitle")
        journal = child_text(article_element, "Journal/Title") or "PubMed"
        published = parse_pubmed_date(article)

        abstract_parts = [clean_text("".join(part.itertext())) for part in descendants(article, "AbstractText")]
        abstract = " ".join(part for part in abstract_parts if part) or NO_ABSTRACT

        author_names = []
        for author in descendants(article, "Author"):
            collective_name = child_text(author, "CollectiveName")
            if collective_name:
                author_names.append(collective_name)
                continue
            last_name = child_text(author, "LastName")
            initials = child_text(author, "Initials")
            full_name = " ".join(part for part in [last_name, initials] if part)
            if full_name:
                author_names.append(full_name)

        if not title or not is_recent(published, start_date, end_date):
            continue
        if not keyword_matches(title, abstract, keywords):
            continue

        papers.append(
            {
                "title": title,
                "journal": journal,
                "authors": ", ".join(author_names) or "Not listed",
                "published": published,
                "abstract": abstract,
                "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                "source": "PubMed",
            }
        )

    return papers


def build_arxiv_query(categories: list[str], keywords: list[str]) -> str:
    """Create an arXiv API search query."""
    category_query = " OR ".join(f"cat:{category}" for category in categories)
    keyword_query = " OR ".join(f'all:"{keyword}"' for keyword in keywords)
    return f"({category_query}) AND ({keyword_query})"


def collect_from_arxiv(
    config: dict[str, Any],
    keywords: list[str],
    start_date: dt.date,
    end_date: dt.date,
) -> list[dict[str, Any]]:
    """Collect matching papers from the arXiv API."""
    if not config.get("enabled", True):
        return []

    categories = config.get("categories", [])
    if not categories:
        return []

    query = build_arxiv_query(categories, keywords)
    response_text = http_get_text(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": query,
            "start": 0,
            "max_results": int(config.get("max_results", 100)),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
    )

    root = ET.fromstring(response_text)
    papers: list[dict[str, Any]] = []

    for entry in descendants(root, "entry"):
        title = child_text(entry, "title")
        abstract = child_text(entry, "summary") or NO_ABSTRACT
        published = parse_date(child_text(entry, "published"))
        link = child_text(entry, "id")
        authors = [child_text(author, "name") for author in descendants(entry, "author")]
        authors = [author for author in authors if author]

        if not title or not is_recent(published, start_date, end_date):
            continue
        if not keyword_matches(title, abstract, keywords):
            continue

        papers.append(
            {
                "title": title,
                "journal": "arXiv",
                "authors": ", ".join(authors) or "Not listed",
                "published": published,
                "abstract": abstract,
                "link": link,
                "source": "arXiv",
            }
        )

    return papers


def deduplicate_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate papers while preserving the first occurrence."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []

    for paper in papers:
        key = normalize_key(paper.get("title", ""), paper.get("link", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(paper)

    return sorted(unique, key=lambda item: (item["published"], item["title"]), reverse=True)


def markdown_escape(value: str) -> str:
    """Escape characters that commonly break Markdown tables or links."""
    return value.replace("|", "\\|").strip()


def write_report(
    papers: list[dict[str, Any]],
    output_dir: Path,
    report_title: str,
    start_date: dt.date,
    end_date: dt.date,
) -> Path:
    """Write the Markdown report and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"papers_{end_date.strftime('%Y_%m_%d')}.md"

    lines = [
        f"# {report_title}",
        "",
        f"Date range: **{start_date.isoformat()}** to **{end_date.isoformat()}**",
        "",
        f"Total papers found: **{len(papers)}**",
        "",
    ]

    if not papers:
        lines.extend(["No matching papers were found for this date range.", ""])
    else:
        for index, paper in enumerate(papers, start=1):
            lines.extend(
                [
                    f"## {index}. {markdown_escape(paper['title'])}",
                    "",
                    f"- **Journal:** {markdown_escape(paper['journal'])}",
                    f"- **Authors:** {markdown_escape(paper['authors'])}",
                    f"- **Publication date:** {paper['published'].isoformat()}",
                    f"- **Source:** {markdown_escape(paper['source'])}",
                    f"- **Link:** {paper['link']}",
                    "",
                    "**Abstract**",
                    "",
                    markdown_escape(paper["abstract"]),
                    "",
                ]
            )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Collect weekly AI + health papers.")
    parser.add_argument("--config", default="config.yaml", help="Path to the YAML configuration file.")
    parser.add_argument("--output-dir", default=".", help="Directory where the Markdown report is written.")
    parser.add_argument("--days", type=int, default=None, help="How many recent days to collect. Default comes from config.yaml.")
    parser.add_argument("--end-date", default=None, help="End date in YYYY-MM-DD format. Default is today's UTC date.")
    return parser.parse_args()


def main() -> int:
    """Run the complete collection workflow."""
    args = parse_args()
    config = load_config(Path(args.config))

    report_config = config.get("report", {})
    days = args.days or int(report_config.get("default_days", 7))
    end_date = dt.date.fromisoformat(args.end_date) if args.end_date else utc_today()
    start_date = end_date - dt.timedelta(days=days - 1)
    keywords = config.get("keywords", [])

    all_papers: list[dict[str, Any]] = []
    collectors = [
        ("RSS", lambda: collect_from_rss(config.get("rss_feeds", []), keywords, start_date, end_date)),
        ("PubMed", lambda: collect_from_pubmed(config.get("pubmed", {}), keywords, start_date, end_date)),
        ("arXiv", lambda: collect_from_arxiv(config.get("arxiv", {}), keywords, start_date, end_date)),
    ]

    for source_name, collector in collectors:
        try:
            papers = collector()
        except (SimpleHttpError, ET.ParseError, json.JSONDecodeError) as error:
            print(f"Warning: {source_name} collection failed: {error}", file=sys.stderr)
            continue
        all_papers.extend(papers)
        print(f"{source_name}: collected {len(papers)} matching papers")

    papers = deduplicate_papers(all_papers)
    report_path = write_report(
        papers=papers,
        output_dir=Path(args.output_dir),
        report_title=report_config.get("title", "Weekly Health AI Papers"),
        start_date=start_date,
        end_date=end_date,
    )

    print(f"Wrote {len(papers)} papers to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
