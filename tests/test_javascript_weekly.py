import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from javascript_weekly_md.archive import (
    IssueSource,
    build_markdown,
    collect_archive_issues,
    collect_feed_issues,
    parse_archive_items,
    parse_article,
    parse_feed,
    parse_feed_items,
)


class JavaScriptWeeklyTests(unittest.TestCase):
    def test_parse_feed_selects_latest_javascript_weekly_item(self):
        feed_xml = textwrap.dedent(
            """\
            <?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0">
              <channel>
                <title>JavaScript Weekly</title>
                <item>
                  <title>npm and pnpm introduce staged publishing</title>
                  <link>https://javascriptweekly.com/issues/787</link>
                  <description><![CDATA[
                    <table><tr><td><p>#787 - May 26, 2026</p></td></tr></table>
                    <table><tr><td><p><a href="https://javascriptweekly.com/link/185664/rss">Staged Publishing for npm Packages Goes Live</a> - npm and pnpm added support.</p></td></tr></table>
                  ]]></description>
                  <pubDate>Tue, 26 May 2026 00:00:00 +0000</pubDate>
                  <guid>https://javascriptweekly.com/issues/787</guid>
                </item>
              </channel>
            </rss>
            """
        )

        issue = parse_feed(feed_xml)

        self.assertEqual(
            issue.title,
            "JavaScript Weekly Issue 787: npm and pnpm introduce staged publishing",
        )
        self.assertEqual(issue.url, "https://javascriptweekly.com/issues/787")
        self.assertEqual(issue.issue_number, "787")
        self.assertEqual(issue.slug_date, "2026-05-26")
        self.assertIn("Staged Publishing", issue.content_html)

    def test_parse_feed_items_returns_all_visible_rss_items_for_year(self):
        feed_xml = textwrap.dedent(
            """\
            <rss>
              <channel>
                <item>
                  <title>npm and pnpm introduce staged publishing</title>
                  <link>https://javascriptweekly.com/issues/787</link>
                  <description>Issue 787 body</description>
                  <pubDate>Tue, 26 May 2026 00:00:00 +0000</pubDate>
                </item>
                <item>
                  <title>Remix 3 drops React</title>
                  <link>https://javascriptweekly.com/issues/784</link>
                  <description>Issue 784 body</description>
                  <pubDate>Tue, 5 May 2026 00:00:00 +0000</pubDate>
                </item>
                <item>
                  <title>Other issue</title>
                  <link>https://javascriptweekly.com/issues/766</link>
                  <description>Issue 766 body</description>
                  <pubDate>Fri, 19 Dec 2025 00:00:00 +0000</pubDate>
                </item>
              </channel>
            </rss>
            """
        )

        issues = parse_feed_items(feed_xml, year=2026)

        self.assertEqual([issue.issue_number for issue in issues], ["787", "784"])
        self.assertEqual([issue.slug_date for issue in issues], ["2026-05-26", "2026-05-05"])

    def test_parse_archive_items_returns_issue_urls_and_dates_for_year(self):
        archive_html = textwrap.dedent(
            """\
            <section class="contained">
              <div class="issues">
                <div class="issue"><a href="issues/787">Issue #787</a> &mdash; May 26, 2026</div>
                <div class="issue"><a href="/issues/786">Issue #786</a> &mdash; May 19, 2026</div>
                <div class="issue"><a href="issues/766">Issue #766</a> &mdash; December 19, 2025</div>
              </div>
            </section>
            """
        )

        issues = parse_archive_items(archive_html, year=2026)

        self.assertEqual([issue.issue_number for issue in issues], ["787", "786"])
        self.assertEqual(
            [issue.url for issue in issues],
            [
                "https://javascriptweekly.com/issues/787",
                "https://javascriptweekly.com/issues/786",
            ],
        )
        self.assertEqual([issue.slug_date for issue in issues], ["2026-05-26", "2026-05-19"])

    def test_article_parser_keeps_newsletter_sections_links_lists_images_and_code(self):
        article_html = textwrap.dedent(
            """\
            <html>
              <head><title>JavaScript Weekly Issue 787: May 26, 2026</title></head>
              <body>
                <div class="subscribe-cta">Get JavaScript Weekly in your inbox</div>
                <div class="issue-html">
                  <table class="el-splitbar"><tr><td><p>#787 - May 26, 2026</p></td></tr></table>
                  <table class="el-masthead"><tr><td><p>JavaScript Weekly</p></td></tr></table>
                  <table class="el-fullwidthimage"><tr><td><img src="https://example.com/hero.jpg" alt="Hero"></td></tr></table>
                  <table class="el-item item"><tr><td>
                    <p class="desc"><span class="mainlink"><a href="https://javascriptweekly.com/link/185664/web">Staged Publishing for npm Packages Goes Live</a></span> - npm and pnpm added support for staged publishing.</p>
                    <p class="name">The npm Project</p>
                  </td></tr></table>
                  <table class="content el-md"><tr><td>
                    <p><strong>IN BRIEF:</strong></p>
                    <ul>
                      <li><p><a href="https://javascriptweekly.com/link/185668/web">Firefox added Web Serial support</a> for hardware from JS.</p></li>
                    </ul>
                  </td></tr></table>
                  <table class="el-heading"><tr><td><p>Articles and Videos</p></td></tr></table>
                  <table class="el-item item"><tr><td>
                    <p class="desc"><span class="mainlink"><a href="https://javascriptweekly.com/link/185677/web">Chrome Previews Declarative Partial Updates</a></span> - New APIs involving <code>setHTML</code>.</p>
                    <p class="name">Chrome Team</p>
                  </td></tr></table>
                </div>
              </body>
            </html>
            """
        )

        article = parse_article(article_html)
        rendered = build_markdown(
            IssueSource(
                title="JavaScript Weekly Issue 787: npm and pnpm introduce staged publishing",
                url="https://javascriptweekly.com/issues/787",
                issue_number="787",
                published_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
            ),
            article,
            fetched_at=datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc),
        )

        self.assertIn("issue_number: \"787\"", rendered)
        self.assertIn("![Hero](https://example.com/hero.jpg)", rendered)
        self.assertIn(
            "[Staged Publishing for npm Packages Goes Live](https://javascriptweekly.com/link/185664/web) - npm and pnpm added support",
            rendered,
        )
        self.assertIn("The npm Project", rendered)
        self.assertIn("**IN BRIEF:**", rendered)
        self.assertIn(
            "- [Firefox added Web Serial support](https://javascriptweekly.com/link/185668/web) for hardware from JS.",
            rendered,
        )
        self.assertIn("## Articles and Videos", rendered)
        self.assertIn("New APIs involving `setHTML`.", rendered)
        self.assertNotIn("Get JavaScript Weekly in your inbox", rendered)
        self.assertNotIn("Read on the Web", rendered)
        self.assertNotIn("## At a Glance", rendered)

    def test_collect_feed_issues_fetches_issue_page_for_canonical_body(self):
        feed_xml = textwrap.dedent(
            """\
            <rss>
              <channel>
                <item>
                  <title>npm and pnpm introduce staged publishing</title>
                  <link>https://javascriptweekly.com/issues/787</link>
                  <description><![CDATA[
                    <div><p><a href="https://javascriptweekly.com/link/185664/rss">RSS Body Item</a> - body from the feed.</p></div>
                  ]]></description>
                  <pubDate>Tue, 26 May 2026 00:00:00 +0000</pubDate>
                </item>
              </channel>
            </rss>
            """
        )

        issue_page = self._issue_page(
            787,
            "May 26, 2026",
            "Canonical Issue Page Item",
        )

        def fake_fetch(url: str, **kwargs):
            if url == "https://cprss.s3.amazonaws.com/javascriptweekly.com.xml":
                return feed_xml
            if url == "https://javascriptweekly.com/issues/787":
                return issue_page
            raise AssertionError(f"unexpected fetch: {url}")

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with patch("javascript_weekly_md.archive.fetch_text", side_effect=fake_fetch):
                paths = collect_feed_issues(
                    output_dir=output_dir,
                    year=2026,
                    fetched_at=datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc),
                )

            self.assertEqual([path.name for path in paths], ["2026-05-26.md"])
            rendered = (output_dir / "2026-05-26.md").read_text(encoding="utf-8")
            self.assertIn("Canonical Issue Page Item", rendered)
            self.assertNotIn("RSS Body Item", rendered)
            self.assertTrue((output_dir / "README.md").exists())

    def test_collect_archive_issues_writes_all_archive_items_for_year(self):
        archive_html = textwrap.dedent(
            """\
            <div class="issues">
              <div class="issue"><a href="issues/787">Issue #787</a> &mdash; May 26, 2026</div>
              <div class="issue"><a href="issues/786">Issue #786</a> &mdash; May 19, 2026</div>
            </div>
            """
        )
        issue_pages = {
            "https://javascriptweekly.com/issues/787": self._issue_page(
                787,
                "May 26, 2026",
                "Staged Publishing for npm Packages Goes Live",
            ),
            "https://javascriptweekly.com/issues/786": self._issue_page(
                786,
                "May 19, 2026",
                "Dr. Axel's blog is gone for now",
            ),
        }

        def fake_fetch(url: str, **kwargs):
            if url == "https://javascriptweekly.com/issues":
                return archive_html
            return issue_pages[url]

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with patch("javascript_weekly_md.archive.fetch_text", side_effect=fake_fetch):
                paths = collect_archive_issues(
                    output_dir=output_dir,
                    year=2026,
                    fetched_at=datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc),
                )

            self.assertEqual(
                [path.name for path in paths],
                ["2026-05-26.md", "2026-05-19.md"],
            )
            self.assertIn(
                "Staged Publishing for npm Packages Goes Live",
                (output_dir / "2026-05-26.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Dr. Axel's blog is gone for now",
                (output_dir / "2026-05-19.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "2026-05-26 - JavaScript Weekly Issue 787",
                (output_dir / "README.md").read_text(encoding="utf-8"),
            )

    def _issue_page(self, issue_number: int, date_label: str, item_title: str) -> str:
        return textwrap.dedent(
            f"""\
            <html>
              <head><title>JavaScript Weekly Issue {issue_number}: {date_label}</title></head>
              <body>
                <div class="issue-html">
                  <table class="el-splitbar"><tr><td><p>#{issue_number} - {date_label}</p></td></tr></table>
                  <table class="el-item item"><tr><td>
                    <p class="desc"><span class="mainlink"><a href="https://example.com/{issue_number}">{item_title}</a></span> - body text.</p>
                  </td></tr></table>
                </div>
              </body>
            </html>
            """
        )


if __name__ == "__main__":
    unittest.main()
