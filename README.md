# javascript-weekly-md

Archive JavaScript Weekly issues as source-oriented Markdown.

## Scope

This project only does the following:

- Reads `https://cprss.s3.amazonaws.com/javascriptweekly.com.xml` to find the latest JavaScript Weekly issue.
- Reads `https://javascriptweekly.com/issues` to discover archive issue URLs for `--all`.
- Fetches each canonical issue page before rendering Markdown, because RSS body content can differ from the issue page.
- Saves issue files under `newsletters/javascript-weekly/weekly/YYYY-MM-DD.md`.
- Maintains `newsletters/javascript-weekly/weekly/README.md` as a newest-first archive index.

It does not translate issues, publish downstream posts, or call model services.

## Local Usage

Fetch the latest issue discovered from the RSS feed:

```bash
python3 -m javascript_weekly_md.archive
```

Fetch every archive-listed issue. Existing files are skipped, so this is also the normal incremental command after the initial backfill:

```bash
python3 -m javascript_weekly_md.archive --all
```

Fetch every archive-listed 2026 issue:

```bash
python3 -m javascript_weekly_md.archive --all --year 2026
```

Fetch a specific issue:

```bash
python3 -m javascript_weekly_md.archive \
  --url https://javascriptweekly.com/issues/787
```

Existing issue files are left unchanged by default. Use `--force` when you intentionally want to regenerate an existing Markdown file.

## Tests

```bash
python3 -m unittest discover -s tests
```

## GitHub Actions

`.github/workflows/javascript-weekly.yml` is written for normal external GitHub Actions, not an internal or self-hosted runner.

- Runner: `ubuntu-latest`
- Network: public outbound internet from the GitHub-hosted runner
- Push trigger: archives every archive-listed JavaScript Weekly issue when pushed to `main`; existing files are skipped
- Schedule: every Tuesday at 13:30 UTC, using the same archive-listed incremental path
- Manual inputs: `newsletter_url` for one issue, or `archive_year` for archive-listed issues from a different year
- Secrets: none
- Commit scope: generated issue Markdown files and the archive index
