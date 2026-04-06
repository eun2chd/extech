from __future__ import annotations

import json
import logging
import sys
import time
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

    round_no = 0
    while True:
        round_no += 1
        log.info("[members] === 순회 라운드 %d (1페이지부터) ===", round_no)
        page = 1
        total_new_round = 0
        while page <= settings.max_list_pages:
            path = list_path_for_page(settings.list_ok_path, page)
            log.info("[members] 페이지 %d", page)
            html = fetch_list_html_at_path(session, settings, path)
            rows = parse_html_table(html, settings.table_selector)
            if not rows:
                log.info(
                    "[members] 빈 페이지 — 끝에 도달했습니다. "
                    "다음 라운드는 다시 1페이지부터 순회합니다.",
                )
                break

            enrich_rows_with_memo(session, settings, rows)

            payloads = []
            for r in rows:
                p = crawl_row_to_member_payload(r)
                if p:
                    payloads.append(p)

            if payloads:
                total_new_round += insert_members_crawled_batch(client, payloads)

            if page_has_list_num_one(rows):
                log.info(
                    "[members] 끝 페이지(번호=1) 도달. "
                    "다음 라운드는 다시 1페이지부터 순회합니다.",
                )
                break

            if page >= settings.max_list_pages:
                log.info(
                    "[members] MAX_LIST_PAGES(%d) 도달 — 이번 라운드 종료",
                    settings.max_list_pages,
                )
                break

            if settings.member_list_page_delay_seconds > 0:
                log.info(
                    "[members] 다음 목록 페이지까지 %.1f초 대기… "
                    "(MEMBER_LIST_PAGE_DELAY_SECONDS)",
                    settings.member_list_page_delay_seconds,
                )
                time.sleep(settings.member_list_page_delay_seconds)

            page += 1

        log.info(
            "[members] 라운드 %d 종료 — 이번 라운드 RPC 영향 행 수 합≈%d (삽입+갱신)",
            round_no,
            total_new_round,
        )
        if not settings.crawl_loop_forever:
            break
        log.info(
            "[members] %.1f초 대기 후 다음 라운드… (CRAWL_LOOP_FOREVER 끄려면 env false)",
            settings.loop_sleep_seconds,
        )
        time.sleep(settings.loop_sleep_seconds)

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

    if settings.skip_supabase:
        log.warning(
            "SKIP_SUPABASE=true 이면 LIST_OK_PATH 를 **한 번만** 요청하고 JSON 만 냅니다. "
            "1~끝페이지 순회 + DB 저장은 SAVE_TO_MEMBERS_CRAWLED=true, SKIP_SUPABASE=false 로 실행하세요.",
        )

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
