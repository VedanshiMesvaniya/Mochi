"""
CLI: crawl every link in a markdown file into the local `crawled_sources`
SQLite table (see app/humor/subreddit_crawler.py).

Usage:
    python scripts/crawl_sources.py path/to/list.md [source_list_name]

Safe to run repeatedly - URLs already stored are skipped (never re-crawled,
never overwritten or deleted).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.humor.subreddit_crawler import crawl_markdown_file  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    md_path = sys.argv[1]
    source_list = sys.argv[2] if len(sys.argv) > 2 else None
    result = crawl_markdown_file(md_path, source_list)
    print(
        f"Done: {result.fetched} fetched, {result.skipped} already stored "
        f"(skipped), {result.failed} failed (will retry next run)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
