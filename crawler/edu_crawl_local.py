"""
교육 목록(edu_list) → `upsert_edu_batch` 후 신청자 → `upsert_edu_applicant_batch`.

신청자 기본: DB `legacy_edu` 의 display_no(숫자) 내림차순으로 각 seq 의 신청 목록 전부 저장.
선택 `--applicants-progress-mode` / EDU_APPLICANTS_PROGRESS_MODE: Edge 와 같이 진행 테이블만 사용.

.env: BASE_URL, LOGIN_PATH, ADMIN_USER, ADMIN_PASSWORD
      SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
선택: EDU_APPLICANT_EDU_DELAY_SECONDS, EDU_APPLICANT_PAGE_DELAY_SECONDS,
      EDU_APPLICANTS_PROGRESS_MODE, EDU_APPLICANTS_ROUNDS (진행 모드 전용)

실행 (프로젝트 루트):
  python -m crawler.edu_crawl_local
  python -m crawler.edu_crawl_local --skip-applicants
  python -m crawler.edu_crawl_local --applicants-progress-mode
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from types import SimpleNamespace

from dotenv import load_dotenv

from supabase import create_client

from crawler.edu_applicants import (
    DEFAULT_EDU_APPLY_TEMPLATE,
    run_applicants_from_saved_legacy_edu,
    run_applicants_phase,
)
from crawler.edu_list_debug import (
    DEFAULT_EDU_LIST_PATH,
    parse_edu_table_with_trs,
    row_to_edu_db_payload,
)
from crawler.list_pager import list_path_for_page
from crawler.session import build_session, fetch_list_html_at_path, login
from crawler.store_edu import upsert_edu_batch

log = logging.getLogger(__name__)


def _page_has_num_one(pairs: list[tuple[object, dict[str, str]]]) -> bool:
    for _, row in pairs:
        v = row.get("번호")
        if v is not None and str(v).strip() == "1":
            return True
    return False


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    p = argparse.ArgumentParser(
        description="Edu list crawl → legacy_edu via RPC (no Edge)",
    )
    p.add_argument("--start-page", type=int, default=1, help="시작 페이지 (기본 1)")
    p.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="이번 실행에서 최대 몇 페이지 (0이면 MAX_LIST_PAGES 환경값)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="페이지마다 upsert 후 대기 초 (기본 0)",
    )
    p.add_argument(
        "--loop",
        action="store_true",
        help="한 바퀴 끝나면 1페이지부터 무한 반복 (끝=빈 페이지·번호=1·max_pages)",
    )
    p.add_argument(
        "--between-rounds",
        type=float,
        default=-1.0,
        help="라운드 사이 대기 초 (기본: env LOOP_SLEEP_SECONDS 또는 10)",
    )
    p.add_argument(
        "--skip-applicants",
        action="store_true",
        help="신청자 단계 생략 (교육 목록만)",
    )
    p.add_argument(
        "--applicants-progress-mode",
        action="store_true",
        help="신청자만 Edge 처럼 edu_applicant_crawl_progress 사용. "
        "기본은 legacy_edu display_no 순으로 전 seq 신청목록 저장",
    )
    p.add_argument(
        "--applicants-rounds",
        type=int,
        default=None,
        metavar="N",
        help="--applicants-progress-mode 일 때만: run_applicants_phase 호출 횟수 (env EDU_APPLICANTS_ROUNDS)",
    )
    args = p.parse_args()

    base = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    login_path = (os.getenv("LOGIN_PATH") or "").strip()
    admin_user = os.getenv("ADMIN_USER") or ""
    admin_pass = os.getenv("ADMIN_PASSWORD") or ""
    supabase_url = (os.getenv("SUPABASE_URL") or "").strip()
    supabase_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()

    if not all([base, login_path, admin_user, admin_pass, supabase_url, supabase_key]):
        log.error(
            "필수: BASE_URL, LOGIN_PATH, ADMIN_USER, ADMIN_PASSWORD, "
            "SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY",
        )
        return 1

    max_pages_env = int(os.getenv("MAX_LIST_PAGES", "2000") or "2000")
    max_pages = args.max_pages if args.max_pages > 0 else max(1, max_pages_env)

    loop_forever = args.loop or (
        (os.getenv("EDU_CRAWL_LOOP", "") or "").lower() in ("1", "true", "yes")
    )
    if args.between_rounds >= 0:
        between_rounds = args.between_rounds
    else:
        try:
            between_rounds = float(os.getenv("LOOP_SLEEP_SECONDS", "10") or "10")
        except ValueError:
            between_rounds = 10.0
    between_rounds = max(0.0, between_rounds)

    user_f = os.getenv("LOGIN_USER_FIELD", "m_id") or "m_id"
    pass_f = os.getenv("LOGIN_PASS_FIELD", "m_pass") or "m_pass"
    verify_tls = (os.getenv("VERIFY_TLS", "true") or "true").lower() in (
        "1",
        "true",
        "yes",
    )
    extra_raw = os.getenv("LOGIN_EXTRA_FIELDS_JSON", "{}") or "{}"
    try:
        login_extra = json.loads(extra_raw)
        if not isinstance(login_extra, dict):
            login_extra = {}
    except json.JSONDecodeError:
        log.warning("LOGIN_EXTRA_FIELDS_JSON invalid; using {}")
        login_extra = {}

    list_base = (os.getenv("EDU_LIST_PATH") or DEFAULT_EDU_LIST_PATH).strip()
    table_sel = (os.getenv("TABLE_SELECTOR") or "table.list_table").strip()
    apply_template = (
        os.getenv("EDU_APPLY_LIST_TEMPLATE") or DEFAULT_EDU_APPLY_TEMPLATE
    ).strip()
    applicant_table_sel = (
        os.getenv("EDU_APPLICANT_TABLE_SELECTOR") or "table.list_table"
    ).strip()
    try:
        pages_per_run_applicants = max(
            1,
            min(50, int(os.getenv("EDU_PAGES_PER_RUN", "1") or "1")),
        )
    except ValueError:
        pages_per_run_applicants = 1

    try:
        applicant_page_delay = float(
            os.getenv("EDU_APPLICANT_PAGE_DELAY_SECONDS", "0") or "0",
        )
    except ValueError:
        applicant_page_delay = 0.0
    applicant_page_delay = max(0.0, applicant_page_delay)

    try:
        applicant_edu_delay = float(
            os.getenv("EDU_APPLICANT_EDU_DELAY_SECONDS", "0") or "0",
        )
    except ValueError:
        applicant_edu_delay = 0.0
    applicant_edu_delay = max(0.0, applicant_edu_delay)

    applicants_progress_mode = args.applicants_progress_mode or (
        (os.getenv("EDU_APPLICANTS_PROGRESS_MODE", "") or "").lower()
        in ("1", "true", "yes")
    )

    settings = SimpleNamespace(
        base_url=base,
        login_path=login_path,
        login_user_field=user_f,
        login_pass_field=pass_f,
        admin_user=admin_user,
        admin_password=admin_pass,
        login_extra_fields=login_extra,
        verify_tls=verify_tls,
        list_method="GET",
        list_post_body=None,
    )

    log.info(
        "[교육] 로컬 크롤 loop=%s max_pages/라운드=%s delay=%ss between_rounds=%ss "
        "applicants=%s 신청자모드=%s pages_per_run(진행모드)=%s page_delay=%ss edu_delay=%ss",
        loop_forever,
        max_pages,
        args.delay,
        between_rounds,
        not args.skip_applicants,
        "진행테이블" if applicants_progress_mode else "DB(display_no순)",
        pages_per_run_applicants,
        applicant_page_delay,
        applicant_edu_delay,
    )
    session = build_session(settings)
    log.info("[교육 목록] 관리자 로그인 시도…")
    login(session, settings)
    log.info("[교육 목록] 로그인 성공")

    client = create_client(supabase_url, supabase_key)

    round_no = 0
    while True:
        round_no += 1
        log.info("[교육 목록] === 라운드 %d (1페이지부터) ===", round_no)
        page = max(1, args.start_page) if round_no == 1 else 1
        pages_done = 0
        while pages_done < max_pages:
            path = list_path_for_page(list_base, page)
            log.info("[교육 목록] 페이지 %d GET %s", page, path)
            html = fetch_list_html_at_path(session, settings, path)
            _headers, pairs, cell_off = parse_edu_table_with_trs(html, table_sel)
            log.info(
                "[교육 목록] 페이지 %d: 표 파싱 완료 (데이터 행 %d, checkbox 오프셋=%d)",
                page,
                len(pairs),
                cell_off,
            )

            if not pairs:
                log.info(
                    "[교육 목록] 빈 페이지 — 끝에 도달했습니다. "
                    "다음 라운드는 다시 1페이지부터 순회합니다.",
                )
                break

            payloads: list[dict] = []
            skipped = 0
            for _tr, row in pairs:
                pl = row_to_edu_db_payload(row)
                if pl.get("_error"):
                    skipped += 1
                    log.warning(
                        "[교육 목록] seq 스킵: %s keys=%s",
                        pl.get("_error"),
                        pl.get("row_keys"),
                    )
                    continue
                payloads.append(pl)

            if skipped:
                log.info(
                    "[교육 목록] seq 없는 행 %d건 제외, upsert 대상 %d건",
                    skipped,
                    len(payloads),
                )

            if payloads:
                upsert_edu_batch(client, payloads)
                log.info("[교육 목록] 페이지 %d insert 처리 끝", page)
            else:
                log.warning("[교육 목록] 페이지 %d: upsert 할 유효 행 없음", page)

            pages_done += 1

            if _page_has_num_one(pairs):
                log.info(
                    "[교육 목록] 끝 페이지(번호=1) 도달. "
                    "다음 라운드는 다시 1페이지부터 순회합니다.",
                )
                break

            if pages_done >= max_pages:
                log.info(
                    "[교육 목록] max_pages=%d 도달 — 이번 라운드 종료. "
                    "다음 라운드는 1페이지부터입니다.",
                    max_pages,
                )
                break

            page += 1
            if args.delay > 0:
                log.info("[교육 목록] 다음 페이지까지 %.1f초 대기 중…", args.delay)
                time.sleep(args.delay)

        log.info("[교육 목록] 라운드 %d 종료", round_no)

        if not args.skip_applicants:
            if applicants_progress_mode:
                if args.applicants_rounds is not None:
                    applicants_rounds = max(1, min(50_000, args.applicants_rounds))
                else:
                    try:
                        applicants_rounds = int(
                            os.getenv("EDU_APPLICANTS_ROUNDS", "1") or "1",
                        )
                    except ValueError:
                        applicants_rounds = 1
                    applicants_rounds = max(1, min(50_000, applicants_rounds))
                log.info(
                    "[교육] 라운드 %d — 신청자 진행테이블 모드 %d회",
                    round_no,
                    applicants_rounds,
                )
                total_app = 0
                for j in range(applicants_rounds):
                    log.info(
                        "[교육] 신청자(run_applicants_phase) %d/%d …",
                        j + 1,
                        applicants_rounds,
                    )
                    total_app += run_applicants_phase(
                        session,
                        settings,
                        client,
                        apply_template=apply_template,
                        applicant_table_sel=applicant_table_sel,
                        max_pages=max_pages,
                        pages_per_run=pages_per_run_applicants,
                        page_delay_seconds=applicant_page_delay,
                    )
                log.info(
                    "[교육] 라운드 %d — 신청자(진행모드) 끝 누적≈%s",
                    round_no,
                    total_app,
                )
            else:
                log.info(
                    "[교육] 라운드 %d — 신청자: legacy_edu display_no 순 전체 seq",
                    round_no,
                )
                total_app = run_applicants_from_saved_legacy_edu(
                    session,
                    settings,
                    client,
                    apply_template=apply_template,
                    applicant_table_sel=applicant_table_sel,
                    max_pages=max_pages,
                    page_delay_seconds=applicant_page_delay,
                    edu_delay_seconds=applicant_edu_delay,
                )
                log.info(
                    "[교육] 라운드 %d — 신청자(DB순회) 끝 누적≈%s",
                    round_no,
                    total_app,
                )
        else:
            log.info("[교육] --skip-applicants 로 신청자 단계 생략")

        if not loop_forever:
            break
        log.info(
            "[교육 목록] %.1f초 대기 후 다음 라운드… (--loop 끄려면 플래그/env 제거)",
            between_rounds,
        )
        time.sleep(between_rounds)

    log.info("[교육 목록] 로컬 크롤 전체 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
