"""Fetch JavaScript Weekly issues and render source-oriented Markdown."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import ProxyHandler, Request, build_opener, urlopen

DEFAULT_FEED_URL = "https://cprss.s3.amazonaws.com/javascriptweekly.com.xml"
DEFAULT_ARCHIVE_URL = "https://javascriptweekly.com/issues"
DEFAULT_OUTPUT_DIR = Path("newsletters/javascript-weekly/weekly")
DEFAULT_USER_AGENT = "javascript-weekly-md/0.1 (+https://javascriptweekly.com/)"
BASE_URL = "https://javascriptweekly.com/"
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass(frozen=True)
class IssueSource:
    title: str
    url: str
    description: str = ""
    author: str = ""
    published_at: datetime | None = None
    image_url: str | None = None
    issue_number: str = ""
    content_html: str = ""

    @property
    def slug_date(self) -> str:
        if self.published_at:
            return self.published_at.date().isoformat()
        parsed_date = _issue_date_from_text(self.title)
        if parsed_date:
            return parsed_date.date().isoformat()
        raise ValueError(f"Cannot derive issue date from URL: {self.url}")


@dataclass
class Bookmark:
    title: str
    url: str
    description: str = ""
    authors: list[str] = field(default_factory=list)


@dataclass
class ContentItem:
    kind: str
    text: str = ""
    url: str = ""
    bookmark: Bookmark | None = None
    language: str = ""


@dataclass
class Section:
    title: str
    items: list[ContentItem] = field(default_factory=list)
    level: int = 2
    excluded: bool = False


@dataclass
class ParsedArticle:
    title: str = ""
    description: str = ""
    author: str = ""
    published_at: datetime | None = None
    image_url: str | None = None
    issue_number: str = ""
    sections: list[Section] = field(default_factory=list)


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self._title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = _attrs_dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = attrs_dict.get("property") or attrs_dict.get("name")
            content = attrs_dict.get("content")
            if key and content:
                self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)

    @property
    def title(self) -> str:
        return _normalize_inline("".join(self._title_parts))


class _ArchiveParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.issues: list[IssueSource] = []
        self._issue_depth = 0
        self._href = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = _attrs_dict(attrs)
        classes = set(attrs_dict.get("class", "").split())
        if self._issue_depth:
            if tag not in VOID_TAGS:
                self._issue_depth += 1
            # Assumes the first <a> inside an issue-card is the subject link.
            # A thumbnail link preceding the subject would break this assumption.
            if tag == "a" and not self._href:
                self._href = attrs_dict.get("href", "")
            return
        if tag == "div" and "issue-card" in classes:
            self._issue_depth = 1
            self._href = ""
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if not self._issue_depth:
            return
        self._issue_depth -= 1
        if self._issue_depth == 0:
            self._finish_issue()

    def handle_data(self, data: str) -> None:
        if self._issue_depth:
            self._parts.append(data)

    def _finish_issue(self) -> None:
        text = _normalize_inline(" ".join(self._parts))
        if not self._href:
            return
        issue_number = _issue_number_from_url(self._href) or _issue_number_from_text(text)
        published_at = _issue_date_from_text(text)
        if not issue_number or not published_at:
            return
        self.issues.append(
            IssueSource(
                title=f"JavaScript Weekly Issue {issue_number}",
                url=urljoin(BASE_URL, self._href),
                published_at=published_at,
                issue_number=issue_number,
            )
        )


class _ArticleParser(HTMLParser):
    def __init__(self, scope_required: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[Section] = [Section("Top Stories")]
        self._current_section = self.sections[0]
        self._scope_required = scope_required
        self._scope_depth = 0
        self._skip_depth = 0
        self._blackout_depth = 0
        self._heading_depth = 0

        self._capture_kind = ""
        self._capture_tag = ""
        self._capture_parts: list[str] = []
        self._capture_language = ""
        self._capture_heading_level = 2
        self._inline_link_stack: list[str] = []
        self._list_stack: list[str] = []

        self._bookmark_depth = 0
        self._bookmark: Bookmark | None = None
        self._bookmark_capture = ""
        self._bookmark_capture_depth = 0
        self._bookmark_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = _attrs_dict(attrs)
        classes = set(attrs_dict.get("class", "").split())
        if not self._enter_scope(tag, classes):
            return

        if self._blackout_depth:
            self._blackout_depth += 1
            return
        if tag in {"script", "style"}:
            self._blackout_depth = 1
            return

        if self._skip_depth:
            if tag not in VOID_TAGS:
                self._skip_depth += 1
            return
        if _is_skip_container(tag, classes):
            self._skip_depth = 1
            return

        if self._heading_depth:
            if tag not in VOID_TAGS:
                self._heading_depth += 1
        elif tag == "table" and "el-heading" in classes:
            self._heading_depth = 1
            return

        if self._bookmark_depth:
            self._handle_bookmark_start(tag, attrs_dict, classes)
            return
        if tag in {"ul", "ol"}:
            self._list_stack.append(tag)
            return
        if tag == "figure" and "kg-bookmark-card" in classes:
            self._bookmark_depth = 1
            self._bookmark = Bookmark(title="", url="")
            return

        if tag == "br" and self._capture_kind:
            self._capture_parts.append("\n")
        elif self._heading_depth and tag == "p":
            self._start_capture("heading", tag, heading_level=2)
        elif tag in {"h2", "h3", "h4", "h5", "h6"}:
            self._start_capture("heading", tag, heading_level=int(tag[1]))
        elif tag == "p":
            self._start_capture("paragraph", tag)
        elif tag == "li":
            kind = "ordered" if self._list_stack and self._list_stack[-1] == "ol" else "bullet"
            self._start_capture(kind, tag)
        elif tag == "blockquote":
            self._start_capture("quote", tag)
        elif tag == "pre":
            self._start_capture("code", tag)
        elif tag == "img":
            self._append_image(attrs_dict)
        elif self._capture_kind:
            self._handle_inline_start(tag, attrs_dict, classes)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._scope_required and self._scope_depth == 0:
            return
        if self._scope_required and tag == "div" and self._scope_depth == 1:
            self._scope_depth = 0
            return

        try:
            if self._blackout_depth:
                self._blackout_depth -= 1
                return
            if self._skip_depth:
                self._skip_depth -= 1
                return
            if self._bookmark_depth:
                self._handle_bookmark_end(tag)
                return

            if self._capture_kind and self._capture_kind != "code":
                self._handle_inline_end(tag)

            if self._capture_kind and tag == self._capture_tag:
                self._finish_capture()
            elif tag in {"ul", "ol"} and self._list_stack:
                self._list_stack.pop()
        finally:
            if self._heading_depth and tag not in VOID_TAGS:
                self._heading_depth -= 1
            if self._scope_required and tag not in VOID_TAGS and self._scope_depth:
                self._scope_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._scope_required and self._scope_depth == 0:
            return
        if self._blackout_depth or self._skip_depth:
            return
        if self._bookmark_depth:
            if self._bookmark_capture:
                self._bookmark_parts.append(data)
            return
        if self._capture_kind:
            self._capture_parts.append(data)

    def _enter_scope(self, tag: str, classes: set[str]) -> bool:
        if not self._scope_required:
            return True
        if self._scope_depth == 0:
            if tag == "div" and "issue-html" in classes:
                self._scope_depth = 1
            return False
        if tag not in VOID_TAGS:
            self._scope_depth += 1
        return True

    def _start_capture(self, kind: str, tag: str, heading_level: int = 2) -> None:
        if self._capture_kind:
            return
        self._capture_kind = kind
        self._capture_tag = tag
        self._capture_parts = []
        self._capture_language = ""
        self._capture_heading_level = heading_level
        self._inline_link_stack = []

    def _finish_capture(self) -> None:
        kind = self._capture_kind
        text = "".join(self._capture_parts)
        language = self._capture_language

        self._capture_kind = ""
        self._capture_tag = ""
        self._capture_parts = []
        self._capture_language = ""
        heading_level = self._capture_heading_level
        self._capture_heading_level = 2
        self._inline_link_stack = []

        if kind == "code":
            normalized = text.strip("\n")
        else:
            normalized = _normalize_inline(text)
        if not normalized:
            return

        if kind == "heading":
            section = Section(normalized, level=heading_level)
            self.sections.append(section)
            self._current_section = section
            return

        if self._current_section.excluded:
            return
        self._current_section.items.append(
            ContentItem(kind=kind, text=normalized, language=language)
        )

    def _append_image(self, attrs_dict: dict[str, str]) -> None:
        src = attrs_dict.get("src", "")
        if not src or self._current_section.excluded:
            return
        self._current_section.items.append(
            ContentItem(kind="image", text=attrs_dict.get("alt", ""), url=src)
        )

    def _handle_inline_start(
        self, tag: str, attrs_dict: dict[str, str], classes: set[str]
    ) -> None:
        if self._capture_kind == "code":
            if tag == "code":
                language = _language_from_classes(classes)
                if language:
                    self._capture_language = language
            return
        if tag == "a":
            self._capture_parts.append("[")
            self._inline_link_stack.append(attrs_dict.get("href", ""))
        elif tag in {"strong", "b"}:
            self._capture_parts.append("**")
        elif tag in {"em", "i", "cite"}:
            self._capture_parts.append("*")
        elif tag == "code":
            self._capture_parts.append("`")
            language = _language_from_classes(classes)
            if language:
                self._capture_language = language

    def _handle_inline_end(self, tag: str) -> None:
        if tag == "a" and self._inline_link_stack:
            href = self._inline_link_stack.pop()
            self._capture_parts.append(f"]({href})" if href else "]")
        elif tag in {"strong", "b"}:
            self._capture_parts.append("**")
        elif tag in {"em", "i", "cite"}:
            self._capture_parts.append("*")
        elif tag == "code":
            self._capture_parts.append("`")

    def _handle_bookmark_start(
        self, tag: str, attrs_dict: dict[str, str], classes: set[str]
    ) -> None:
        if self._bookmark is None:
            return
        if tag == "a" and not self._bookmark.url:
            self._bookmark.url = attrs_dict.get("href", "")

        field_name = ""
        if "kg-bookmark-title" in classes:
            field_name = "title"
        elif "kg-bookmark-description" in classes:
            field_name = "description"
        elif "kg-bookmark-author" in classes:
            field_name = "author"
        elif "kg-bookmark-publisher" in classes:
            field_name = "publisher"

        if tag not in VOID_TAGS:
            self._bookmark_depth += 1

        if field_name and not self._bookmark_capture:
            self._bookmark_capture = field_name
            self._bookmark_capture_depth = 1
            self._bookmark_parts = []
        elif self._bookmark_capture and tag not in VOID_TAGS:
            self._bookmark_capture_depth += 1

    def _handle_bookmark_end(self, tag: str) -> None:
        if self._bookmark_capture:
            self._bookmark_capture_depth -= 1
            if self._bookmark_capture_depth == 0 and self._bookmark:
                value = _normalize_inline("".join(self._bookmark_parts))
                if value:
                    if self._bookmark_capture == "title":
                        self._bookmark.title = value
                    elif self._bookmark_capture == "description":
                        self._bookmark.description = value
                    else:
                        self._bookmark.authors.append(value)
                self._bookmark_capture = ""
                self._bookmark_parts = []

        self._bookmark_depth -= 1
        if self._bookmark_depth == 0 and self._bookmark:
            if (
                self._bookmark.title
                and self._bookmark.url
                and not self._current_section.excluded
            ):
                self._current_section.items.append(
                    ContentItem(kind="bookmark", bookmark=self._bookmark)
                )
            self._bookmark = None


def parse_feed(feed_xml: str) -> IssueSource:
    """Return the first JavaScript Weekly issue from the RSS feed."""
    issues = parse_feed_items(feed_xml)
    if not issues:
        raise ValueError("No JavaScript Weekly issue found in feed")
    return issues[0]


def parse_feed_items(feed_xml: str, year: int | None = None) -> list[IssueSource]:
    """Return every JavaScript Weekly issue visible in the RSS feed."""
    root = ET.fromstring(feed_xml)
    issues: list[IssueSource] = []
    for item in root.findall("./channel/item"):
        title = _xml_text(item, "title")
        link = _xml_text(item, "link")
        if not _is_weekly_item(title, link, []):
            continue

        published = _parse_rfc2822_datetime(_xml_text(item, "pubDate"))
        content_html = _xml_raw_text(item, "description")
        issue_number = _issue_number_from_url(link) or _issue_number_from_text(content_html)
        issue = IssueSource(
            title=_compose_issue_title(issue_number, title),
            url=link,
            description=title,
            published_at=published,
            issue_number=issue_number,
            content_html=content_html,
        )
        if year is None or _issue_year(issue) == year:
            issues.append(issue)
    return issues


def parse_archive_items(archive_html: str, year: int | None = None) -> list[IssueSource]:
    """Return issue links listed on the JavaScript Weekly archive page."""
    parser = _ArchiveParser()
    parser.feed(archive_html)
    if year is None:
        return parser.issues
    return [issue for issue in parser.issues if _issue_year(issue) == year]


def parse_article(html: str) -> ParsedArticle:
    meta_parser = _MetaParser()
    meta_parser.feed(html)
    meta = meta_parser.meta

    article_parser = _ArticleParser(scope_required="issue-html" in html)
    article_parser.feed(html)

    sections = [
        section
        for section in article_parser.sections
        if section.items or section.title != "Top Stories"
    ]
    image_url = meta.get("og:image") or meta.get("twitter:image") or _first_image_url(sections)
    title = meta.get("og:title") or meta.get("twitter:title") or meta_parser.title
    issue_number = _issue_number_from_text(title) or _issue_number_from_text(html)
    published_at = (
        _parse_iso_datetime(meta.get("article:published_time", ""))
        or _issue_date_from_text(title)
        or _issue_date_from_text(html)
    )
    return ParsedArticle(
        title=title,
        description=meta.get("og:description") or meta.get("twitter:description") or "",
        author=meta.get("author") or meta.get("twitter:data1") or "",
        published_at=published_at,
        image_url=image_url,
        issue_number=issue_number,
        sections=sections,
    )


def issue_from_article(url: str, article: ParsedArticle) -> IssueSource:
    issue_number = article.issue_number or _issue_number_from_url(url)
    return IssueSource(
        title=article.title or _compose_issue_title(issue_number, ""),
        url=url,
        description=article.description,
        author=article.author,
        published_at=article.published_at,
        image_url=article.image_url,
        issue_number=issue_number,
    )


def build_markdown(
    issue: IssueSource,
    article: ParsedArticle,
    fetched_at: datetime | None = None,
) -> str:
    fetched = fetched_at or datetime.now(timezone.utc)
    title = issue.title or article.title
    description = issue.description or article.description
    published = issue.published_at or article.published_at
    author = issue.author or article.author
    image_url = issue.image_url or article.image_url
    issue_number = issue.issue_number or article.issue_number

    lines: list[str] = [
        "---",
        f"title: {_yaml_scalar(title)}",
        f"url: {_yaml_scalar(issue.url)}",
    ]
    if issue_number:
        lines.append(f"issue_number: {_yaml_scalar(issue_number)}")
    lines.extend(
        [
            f"published_at: {_yaml_scalar(_isoformat(published))}",
            f"fetched_at: {_yaml_scalar(_isoformat(fetched))}",
            f"description: {_yaml_scalar(description)}",
        ]
    )
    if author:
        lines.append(f"author: {_yaml_scalar(author)}")
    if image_url:
        lines.append(f"image: {_yaml_scalar(image_url)}")
    lines.extend(["---", "", f"# {title}", ""])

    if description:
        lines.extend([f"> {description}", ""])

    for section in article.sections:
        lines.extend(_render_section(section))

    return "\n".join(lines).rstrip() + "\n"


def write_issue(
    output_dir: Path,
    issue: IssueSource,
    article: ParsedArticle,
    fetched_at: datetime | None = None,
    overwrite: bool = False,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{issue.slug_date}.md"
    if output_path.exists() and not overwrite:
        update_index(output_dir)
        return output_path
    output_path.write_text(build_markdown(issue, article, fetched_at), encoding="utf-8")
    update_index(output_dir)
    return output_path


def update_index(output_dir: Path, index_name: str = "README.md") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for markdown_path in output_dir.glob("*.md"):
        if markdown_path.name == index_name:
            continue
        frontmatter = _read_frontmatter(markdown_path)
        if not frontmatter:
            continue
        entries.append(
            {
                "path": markdown_path,
                "date": markdown_path.stem,
                "title": frontmatter.get("title", markdown_path.stem),
                "url": frontmatter.get("url", ""),
                "published_at": frontmatter.get("published_at", ""),
                "description": frontmatter.get("description", ""),
            }
        )

    entries.sort(key=lambda item: (item["date"], item["published_at"]), reverse=True)
    lines = [
        "# JavaScript Weekly Archive",
        "",
        "Generated Markdown archive from JavaScript Weekly issue pages.",
        "",
    ]
    if entries:
        for entry in entries:
            description = f" - {entry['description']}" if entry["description"] else ""
            lines.append(
                f"- [{entry['date']} - {entry['title']}]({entry['path'].name}){description}"
            )
    else:
        lines.append("_No issues have been generated yet._")
    lines.append("")

    index_path = output_dir / index_name
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


def fetch_text(url: str, user_agent: str = DEFAULT_USER_AGENT, timeout: int = 30) -> str:
    try:
        return _fetch_text_once(url, user_agent=user_agent, timeout=timeout)
    except URLError as error:
        if not _is_proxy_tunnel_forbidden(error):
            raise
        print(
            f"Default proxy rejected {url}; retrying without proxy.",
            file=sys.stderr,
        )
        return _fetch_text_once(
            url,
            user_agent=user_agent,
            timeout=timeout,
            ignore_proxy=True,
        )


def collect_issue(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    url: str = "",
    feed_url: str = DEFAULT_FEED_URL,
    fetched_at: datetime | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    overwrite: bool = False,
) -> Path:
    if url:
        article_html = fetch_text(url, user_agent=user_agent)
        article = parse_article(article_html)
        issue = issue_from_article(url, article)
    else:
        issue = parse_feed(fetch_text(feed_url, user_agent=user_agent))
        article = parse_article(fetch_text(issue.url, user_agent=user_agent))
    return write_issue(output_dir, issue, article, fetched_at=fetched_at, overwrite=overwrite)


def collect_feed_issues(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    feed_url: str = DEFAULT_FEED_URL,
    year: int | None = None,
    fetched_at: datetime | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    overwrite: bool = False,
) -> list[Path]:
    issues = parse_feed_items(fetch_text(feed_url, user_agent=user_agent), year=year)
    return _collect_issue_pages(
        issues=issues,
        output_dir=output_dir,
        fetched_at=fetched_at,
        user_agent=user_agent,
        overwrite=overwrite,
    )


def collect_archive_issues(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    archive_url: str = DEFAULT_ARCHIVE_URL,
    year: int | None = None,
    fetched_at: datetime | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    overwrite: bool = False,
) -> list[Path]:
    issues = parse_archive_items(fetch_text(archive_url, user_agent=user_agent), year=year)
    return _collect_issue_pages(
        issues=issues,
        output_dir=output_dir,
        fetched_at=fetched_at,
        user_agent=user_agent,
        overwrite=overwrite,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch JavaScript Weekly and save issues as structured Markdown."
    )
    parser.add_argument(
        "--url",
        default="",
        help="Specific newsletter URL. If omitted, the latest RSS issue is used.",
    )
    parser.add_argument("--feed-url", default=DEFAULT_FEED_URL)
    parser.add_argument("--archive-url", default=DEFAULT_ARCHIVE_URL)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Archive every JavaScript Weekly issue currently listed on the archive page.",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="When used with --all, archive only archive-listed issues from this year.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Markdown output directory. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--fetched-at",
        default="",
        help="ISO timestamp override for reproducible output.",
    )
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing issue file instead of leaving it unchanged.",
    )
    args = parser.parse_args(argv)

    if args.url and args.all:
        parser.error("--url and --all cannot be used together")

    fetched_at = _parse_iso_datetime(args.fetched_at) if args.fetched_at else None
    if args.all:
        output_paths = collect_archive_issues(
            output_dir=args.output_dir,
            archive_url=args.archive_url,
            year=args.year,
            fetched_at=fetched_at,
            user_agent=args.user_agent,
            overwrite=args.force,
        )
        for output_path in output_paths:
            print(output_path)
    else:
        output_path = collect_issue(
            output_dir=args.output_dir,
            url=args.url,
            feed_url=args.feed_url,
            fetched_at=fetched_at,
            user_agent=args.user_agent,
            overwrite=args.force,
        )
        print(output_path)
    return 0


def _collect_issue_pages(
    issues: list[IssueSource],
    output_dir: Path,
    fetched_at: datetime | None,
    user_agent: str,
    overwrite: bool,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if not issues:
        update_index(output_dir)
        return []

    output_paths: list[Path] = []
    for issue in issues:
        output_path = output_dir / f"{issue.slug_date}.md"
        if output_path.exists() and not overwrite:
            output_paths.append(output_path)
            continue
        article = parse_article(fetch_text(issue.url, user_agent=user_agent))
        output_paths.append(
            write_issue(
                output_dir,
                _issue_with_article_defaults(issue, article),
                article,
                fetched_at=fetched_at,
                overwrite=overwrite,
            )
        )
    update_index(output_dir)
    return output_paths


def _issue_with_article_defaults(issue: IssueSource, article: ParsedArticle) -> IssueSource:
    title = issue.title or article.title
    if _is_bare_issue_title(issue.title, issue.issue_number) and article.title:
        title = article.title
    return IssueSource(
        title=title,
        url=issue.url,
        description=issue.description or article.description,
        author=issue.author or article.author,
        published_at=issue.published_at or article.published_at,
        image_url=issue.image_url or article.image_url,
        issue_number=issue.issue_number or article.issue_number,
        content_html=issue.content_html,
    )


def _fetch_text_once(
    url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = 30,
    ignore_proxy: bool = False,
) -> str:
    request = Request(url, headers={"User-Agent": user_agent})
    opener = build_opener(ProxyHandler({})) if ignore_proxy else None
    response_context = opener.open(request, timeout=timeout) if opener else urlopen(request, timeout=timeout)
    with response_context as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, "replace")


def _render_section(section: Section) -> list[str]:
    if section.excluded:
        return []
    level = max(2, min(section.level, 6))
    lines = [f"{'#' * level} {section.title}", ""]
    previous_kind = ""
    ordered_index = 1
    for item in section.items:
        if previous_kind in {"bullet", "ordered"} and item.kind != previous_kind and lines[-1] != "":
            lines.append("")
        if item.kind != "ordered":
            ordered_index = 1
        if item.kind == "paragraph":
            lines.extend([item.text, ""])
        elif item.kind == "bullet":
            lines.append(f"- {item.text}")
        elif item.kind == "ordered":
            lines.append(f"{ordered_index}. {item.text}")
            ordered_index += 1
        elif item.kind == "quote":
            lines.extend([_render_quote(item.text), ""])
        elif item.kind == "code":
            lines.extend([_render_code(item.text, item.language), ""])
        elif item.kind == "image":
            lines.extend([f"![{item.text}]({item.url})", ""])
        elif item.kind == "bookmark" and item.bookmark:
            lines.append(_render_bookmark(item.bookmark))
        previous_kind = item.kind
    if lines[-1] != "":
        lines.append("")
    return lines


def _render_bookmark(bookmark: Bookmark) -> str:
    description = bookmark.description
    metadata = ""
    if bookmark.authors:
        metadata = " " + "_(" + " / ".join(dict.fromkeys(bookmark.authors)) + ")_"
    suffix = f" - {description}" if description else ""
    return f"- [{bookmark.title}]({bookmark.url}){suffix}{metadata}"


def _render_quote(text: str) -> str:
    return "\n".join(f"> {line}" for line in text.splitlines())


def _render_code(text: str, language: str) -> str:
    return f"```{language}\n{text}\n```"


def _is_skip_container(tag: str, classes: set[str]) -> bool:
    skip_classes = {
        "el-splitbar",
        "el-masthead",
        "norss",
        "pager",
        "subscribe-cta",
    }
    return tag in {"header", "nav", "footer", "form"} or bool(skip_classes & classes)


def _is_weekly_item(title: str, link: str, categories: Iterable[str]) -> bool:
    del title, categories
    return "/issues/" in link


def _issue_year(issue: IssueSource) -> int | None:
    if issue.published_at:
        return issue.published_at.year
    parsed_date = _issue_date_from_text(issue.title)
    return parsed_date.year if parsed_date else None


def _xml_text(parent: ET.Element, tag_name: str) -> str:
    return _clean_text(_xml_raw_text(parent, tag_name))


def _xml_raw_text(parent: ET.Element, tag_name: str) -> str:
    child = parent.find(tag_name)
    return child.text if child is not None and child.text else ""


def _attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {key: value or "" for key, value in attrs}


def _language_from_classes(classes: set[str]) -> str:
    for class_name in classes:
        if class_name.startswith("language-"):
            return class_name.removeprefix("language-")
    return ""


def _normalize_inline(text: str) -> str:
    normalized = text.replace("\u200b", "").replace("\xa0", " ")
    normalized = " ".join(normalized.split())
    normalized = normalized.replace("[ ", "[").replace(" ](", "](")
    return normalized.strip()


def _clean_text(text: str) -> str:
    return _normalize_inline(text)


def _parse_rfc2822_datetime(value: str) -> datetime | None:
    if not value:
        return None
    parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_issue_date_label(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _issue_date_from_text(text: str) -> datetime | None:
    # "Month DD, YYYY" is checked before ISO dates: parse_article's html fallback
    # can otherwise match a stray ISO date embedded in article body code.
    for pattern in (r"[A-Z][a-z]+ \d{1,2}, \d{4}", r"\d{4}-\d{2}-\d{2}"):
        match = re.search(pattern, text)
        if match:
            return _parse_issue_date_label(match.group(0))
    return None


def _issue_number_from_url(url: str) -> str:
    match = re.search(r"/issues/(\d+)", url)
    return match.group(1) if match else ""


def _issue_number_from_text(text: str) -> str:
    for pattern in (r"Issue\s*#?\s*(\d+)", r"#\s*\u200b?\s*(\d+)"):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _compose_issue_title(issue_number: str, headline: str) -> str:
    if headline.startswith("JavaScript Weekly Issue"):
        return headline
    if issue_number and headline:
        return f"JavaScript Weekly Issue {issue_number}: {headline}"
    if issue_number:
        return f"JavaScript Weekly Issue {issue_number}"
    return headline or "JavaScript Weekly"


def _is_bare_issue_title(title: str, issue_number: str) -> bool:
    return bool(issue_number) and title == f"JavaScript Weekly Issue {issue_number}"


def _first_image_url(sections: list[Section]) -> str | None:
    for section in sections:
        for item in section.items:
            if item.kind == "image" and item.url:
                return item.url
    return None


def _isoformat(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _yaml_scalar(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def _read_frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return {}
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            break
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value.strip('"')
        values[key.strip()] = value
    return values


def _is_proxy_tunnel_forbidden(error: URLError) -> bool:
    reason = getattr(error, "reason", "")
    return "Tunnel connection failed: 403" in str(reason)


if __name__ == "__main__":
    raise SystemExit(main())
