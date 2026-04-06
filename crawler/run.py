from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from crawler.config import load_settings
from crawler.list_pager import list_path_for_page
from crawler.member_memo import enrich_rows_with_memo
from crawler.members_map import crawl_row_to_member_payload, page_has_list_num_one
from crawler.parse_table import parse_html_table
from crawler.session import (
    build_session,
    fetch_list_html,
    fetch_list_html_at_path,
    login,
)
from crawler.store import make_supabase, upsert_rows
from crawler.store_members import insert_members_crawled_batch


def _run_members_paginated(log: logging.Logger, settings) -> int:
    if settings.skip_supabase:
        log.error("SAVE_TO_MEMBERS_CRAWLED requires SKIP_SUPABASE=false")
        return 1

    session = build_session(settings)
    login(session, settings)
    client = make_supabase(settings)

    page = 1
    total_new = 0
    while page <= settings.max_list_pages:
        path = list_path_for_page(settings.list_ok_path, page)
        log.info("members crawl page %d", page)
        html = fetch_list_html_at_path(session, settings, path)
        rows = parse_html_table(html, settings.table_selector)
        if not rows:
            log.warning("empty list on page %d, stopping", page)
            break

        enrich_rows_with_memo(session, settings, rows)

        payloads = []
        for r in rows:
            p = crawl_row_to_member_payload(r)
            if p:
                payloads.append(p)

        if payloads:
            total_new += insert_members_crawled_batch(client, payloads)

        if page_has_list_num_one(rows):
            log.info("last page reached (번호=1 seen on page %d)", page)
            break

        page += 1

    log.info("members crawl finished: ~%d new rows inserted this run", total_new)
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)
    try:
        settings = load_settings()
    except RuntimeError as e:
        log.error("%s", e)
        return 1

    if settings.save_to_members_crawled:
        return _run_members_paginated(log, settings)

    session = build_session(settings)
    login(session, settings)
    html = fetch_list_html(session, settings)
    rows = parse_html_table(html, settings.table_selector)
    enrich_rows_with_memo(session, settings, rows)

    if settings.skip_supabase:
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass
        out = json.dumps(rows, ensure_ascii=False, indent=2)
        print(out)
        preview = Path("_debug") / "last_crawl.json"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_text(out, encoding="utf-8")
        log.info(
            "SKIP_SUPABASE=true — %d rows (terminal + %s), DB not used",
            len(rows),
            preview.resolve(),
        )
        return 0

    client = make_supabase(settings)
    upsert_rows(client, settings, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
