"""
교육신청관리 목록(edu_apply_list.html) + 신청자 상세(edu_apply_form.html) 크롤 →
public.edu_apply / public.edu_apply_user (1:1).

상세는 폼의 개인정보·재직(회사) 필드만 저장 (첨부파일 제외).

.env: BASE_URL, LOGIN_PATH, ADMIN_USER, ADMIN_PASSWORD
      SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
      (SKIP_SUPABASE=true 이면 DB 없이 stdout JSON)

선택:
  EDU_APPLY_MANAGE_LIST_PATH — 기본 /admin/edu/edu_apply_list.html?page=1
  EDU_APPLY_MANAGE_TABLE_SELECTOR — 기본 table.list_table
  EDU_APPLY_DETAIL_PATH_TEMPLATE — 기본 /admin/edu/edu_apply_form.html?mode=modify&seq={seq}&page=1

실행 (프로젝트 루트):
  python -m crawler.edu_apply_management_crawl
  python -m crawler.edu_apply_management_crawl --max-pages 3 --detail-delay 0.7
  python -m crawler.edu_apply_management_crawl --skip-detail
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from types import SimpleNamespace
from typing import Any

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import Client, create_client

from crawler.edu_applicants import _rpc_safe_timestamp, extract_applicant_user_id
from crawler.edu_list_debug import parse_edu_table_with_trs, pick, strip_nbsp
from crawler.list_pager import list_path_for_page
from crawler.session import build_session, fetch_list_html_at_path, login, resolve_url

log = logging.getLogger(__name__)

DEFAULT_EDU_APPLY_MANAGE_LIST_PATH = "/admin/edu/edu_apply_list.html?page=1"
DEFAULT_DETAIL_TEMPLATE = (
    "/admin/edu/edu_apply_form.html?mode=modify&seq={seq}&page=1"
)

_EDU_PERIOD_RANGE = re.compile(
    r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*[~～∼]\s*"
    r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})",
)
_RESUME_SEQ = re.compile(r"resume_form\.html\?[^\"']*seq=(\d+)", re.I)


def _page_has_num_one(pairs: list[tuple[object, dict[str, str]]]) -> bool:
    for _, row in pairs:
        if str(row.get("번호") or "").strip() == "1":
            return True
    return False


def extract_resume_seq_from_tr(tr: Any) -> int | None:
    html = str(tr)
    m = _RESUME_SEQ.search(html)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def parse_edu_period_to_dates(raw: str) -> tuple[str | None, str | None]:
    s = strip_nbsp(raw)
    if not s:
        return None, None
    m = _EDU_PERIOD_RANGE.search(s)
    if not m:
        return None, None
    y1, mo1, d1 = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
    y2, mo2, d2 = m.group(4), m.group(5).zfill(2), m.group(6).zfill(2)
    return f"{y1}-{mo1}-{d1}", f"{y2}-{mo2}-{d2}"


def _int_or_none(s: str | None) -> int | None:
    if s is None:
        return None
    t = re.sub(r"[,\s_]", "", str(s).strip())
    if not t or not t.isdigit():
        return None
    return int(t)


def row_to_edu_apply_payload(
    tr: Any,
    row: dict[str, str],
) -> dict[str, Any] | None:
    seq_raw = (row.get("_seq") or "").strip()
    if not seq_raw.isdigit():
        return None
    seq = int(seq_raw)

    no_raw = pick(row, ["번호", "No", "no"])
    list_no: int | None = None
    if no_raw and str(no_raw).strip().isdigit():
        list_no = int(str(no_raw).strip())

    period_raw = pick(row, ["교육기간", "교육_기간"])
    start_d, end_d = parse_edu_period_to_dates(period_raw)

    user_id = extract_applicant_user_id(row)
    ca = _rpc_safe_timestamp(
        pick(row, ["등록일자", "등록일", "신청일", "created_at"]),
    )

    pl: dict[str, Any] = {
        "seq": seq,
        "list_no": list_no,
        "branch": pick(row, ["신청지사", "지사", "branch"]) or None,
        "category": pick(row, ["구분", "유형", "category"]) or None,
        "edu_name": pick(row, ["교육명", "강좌명"]) or None,
        "edu_start_date": start_d,
        "edu_end_date": end_d,
        "user_name": pick(row, ["성명", "이름"]) or None,
        "user_id": user_id or None,
        "phone": pick(row, ["연락처", "휴대폰", "phone"]) or None,
        "apply_status": pick(row, ["접수상태", "신청상태"]) or None,
        "exam_status": pick(row, ["시험상태"]) or None,
        "payment_status": pick(row, ["결제", "결제상태", "입금상태"]) or None,
        "resume_seq": extract_resume_seq_from_tr(tr),
        "created_at": ca,
    }
    return pl


def _input_value(soup: BeautifulSoup, el_id: str) -> str | None:
    el = soup.find("input", id=el_id)
    if el is None:
        el = soup.find("input", attrs={"name": el_id})
    if el is None:
        return None
    v = el.get("value")
    if v is None:
        return ""
    return str(v).strip()


def _detail_has_values(detail: dict[str, Any]) -> bool:
    for v in detail.values():
        if v is None:
            continue
        if str(v).strip():
            return True
    return False


def parse_edu_apply_form(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    price_raw = _input_value(soup, "eal_price")
    return {
        "price": _int_or_none(price_raw),
        "user_login_id": _input_value(soup, "eal_id") or None,
        "first_name": _input_value(soup, "eal_firstname") or None,
        "last_name": _input_value(soup, "eal_lastname") or None,
        "passport_first_name": _input_value(soup, "eal_passport_fname") or None,
        "passport_last_name": _input_value(soup, "eal_passport_lname") or None,
        "birth": _input_value(soup, "eal_birth") or None,
        "email": _input_value(soup, "eal_email") or None,
        "phone_hp": _input_value(soup, "eal_hp") or None,
        "phone_tel": _input_value(soup, "eal_tel") or None,
        "addr_postal": _input_value(soup, "eal_addrport") or None,
        "addr1": _input_value(soup, "eal_addr1") or None,
        "addr2": _input_value(soup, "eal_addr2") or None,
        "company_name": _input_value(soup, "eal_company_name") or None,
        "company_department": _input_value(soup, "eal_company_dep") or None,
        "company_rank": _input_value(soup, "eal_company_rank") or None,
        "company_addr_postal": _input_value(soup, "eal_company_addrport") or None,
        "company_addr1": _input_value(soup, "eal_company_addr1") or None,
        "company_addr2": _input_value(soup, "eal_company_addr2") or None,
    }


def fetch_get_html(session: Any, settings: Any, path: str) -> str:
    url = resolve_url(settings.base_url, path)
    log.info("GET %s", path)
    r = session.get(url, timeout=120)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding or "utf-8"
    return r.text


def upsert_edu_apply_and_user(
    client: Client | None,
    skip_supabase: bool,
    session: Any,
    settings: Any,
    list_payload: dict[str, Any],
    detail_template: str,
    fetch_detail: bool,
    detail_delay: float,
) -> None:
    detail: dict[str, Any] | None = None
    seq = list_payload["seq"]
    if fetch_detail:
        path = detail_template.format(seq=seq)
        try:
            html = fetch_get_html(session, settings, path)
            detail = parse_edu_apply_form(html)
        except Exception:
            log.exception("상세 폼 실패 seq=%s", seq)
            detail = None
        if detail_delay > 0:
            time.sleep(detail_delay)

    if skip_supabase:
        print(
            json.dumps(
                {"edu_apply": list_payload, "edu_apply_user": detail},
                ensure_ascii=False,
                default=str,
            ),
        )
        return

    assert client is not None
    # supabase-py: upsert() 뒤에 .select() 체이닝이 안 되는 버전이 있어 upsert 후 seq 로 id 조회
    client.table("edu_apply").upsert(
        list_payload,
        on_conflict="seq",
    ).execute()
    res = (
        client.table("edu_apply")
        .select("id")
        .eq("seq", seq)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    if not rows:
        log.warning("edu_apply upsert 후 id 조회 실패 seq=%s", seq)
        return
    eid = rows[0].get("id")
    if eid is None:
        log.warning("edu_apply id null seq=%s", seq)
        return

    if not detail or not _detail_has_values(detail):
        return

    detail_row = {**detail, "edu_apply_id": eid}
    client.table("edu_apply_user").upsert(
        detail_row,
        on_conflict="edu_apply_id",
    ).execute()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    p = argparse.ArgumentParser(
        description="교육신청관리 목록+상세 → edu_apply / edu_apply_user",
    )
    p.add_argument("--start-page", type=int, default=1)
    p.add_argument("--max-pages", type=int, default=1)
    p.add_argument("--detail-delay", type=float, default=0.0)
    p.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="페이지당 최대 행 수 (0=전체, 디버그용)",
    )
    p.add_argument("--skip-detail", action="store_true")
    args = p.parse_args()

    base = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    login_path = (os.getenv("LOGIN_PATH") or "").strip()
    admin_user = os.getenv("ADMIN_USER") or ""
    admin_pass = os.getenv("ADMIN_PASSWORD") or ""
    supabase_url = (os.getenv("SUPABASE_URL") or "").strip()
    supabase_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    skip_sb = (os.getenv("SKIP_SUPABASE", "") or "").lower() in (
        "1",
        "true",
        "yes",
    )

    if not all([base, login_path, admin_user, admin_pass]):
        log.error("필수: BASE_URL, LOGIN_PATH, ADMIN_USER, ADMIN_PASSWORD")
        return 1
    if not skip_sb and (not supabase_url or not supabase_key):
        log.error("Supabase: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY 또는 SKIP_SUPABASE=true")
        return 1

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
        login_extra = {}

    list_base = (
        os.getenv("EDU_APPLY_MANAGE_LIST_PATH") or DEFAULT_EDU_APPLY_MANAGE_LIST_PATH
    ).strip()
    table_sel = (
        os.getenv("EDU_APPLY_MANAGE_TABLE_SELECTOR") or "table.list_table"
    ).strip()
    detail_tpl = (
        os.getenv("EDU_APPLY_DETAIL_PATH_TEMPLATE") or DEFAULT_DETAIL_TEMPLATE
    ).strip()

    max_pages = max(1, args.max_pages)
    start_page = max(1, args.start_page)

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

    session = build_session(settings)
    login(session, settings)
    log.info("로그인 완료 — 교육신청관리 목록 크롤 시작")

    client: Client | None = None
    if not skip_sb:
        client = create_client(supabase_url, supabase_key)

    page = start_page
    pages_done = 0
    fetch_detail = not args.skip_detail

    while pages_done < max_pages:
        path = list_path_for_page(list_base, page)
        html = fetch_list_html_at_path(session, settings, path)
        _headers, pairs, _off = parse_edu_table_with_trs(html, table_sel)
        log.info("페이지 %d: 데이터 행 %d건", page, len(pairs))

        if not pairs:
            log.info("빈 페이지 — 종료")
            break

        lim = len(pairs) if args.max_rows <= 0 else min(args.max_rows, len(pairs))
        for tr, row in pairs[:lim]:
            pl = row_to_edu_apply_payload(tr, row)
            if pl is None:
                log.warning("seq 없음, 스킵 keys=%s", list(row.keys()))
                continue
            upsert_edu_apply_and_user(
                client,
                skip_sb,
                session,
                settings,
                pl,
                detail_tpl,
                fetch_detail,
                args.detail_delay,
            )

        pages_done += 1
        if _page_has_num_one(pairs):
            log.info("번호=1 행 포함 — 마지막 페이지로 보고 종료")
            break
        if pages_done >= max_pages:
            break
        page += 1

    log.info("교육신청관리 크롤 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
