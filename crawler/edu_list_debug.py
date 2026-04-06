"""
ex-tech 교육 목록(edu_list) 한 페이지를 로그인 후 가져와 파싱 결과를 JSON 으로 출력.
각 행의 upsert_row 는 Edge edu-crawl 이 legacy_edu 에 넣는 페이로드와 동일한 키를 씀.
(unit 은 교육명 선두 괄호 안만 추출·괄호 제거, title 은 셀 원문.)

.env 필요: BASE_URL, LOGIN_PATH, ADMIN_USER, ADMIN_PASSWORD
         (ex-tech 는 LOGIN_USER_FIELD=m_id, LOGIN_PASS_FIELD=m_pass)

선택: EDU_LIST_PATH — 기본은 아래 full query (page 는 --page 로 치환)
      TABLE_SELECTOR — 기본 table.list_table
      LOGIN_EXTRA_FIELDS_JSON — 로그인 POST 추가 필드 (crawler.config 와 동일)

실행 (프로젝트 루트):
  python -m crawler.edu_list_debug
  python -m crawler.edu_list_debug --page 134 --limit 3
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from types import SimpleNamespace

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from crawler.list_pager import list_path_for_page
from crawler.session import build_session, fetch_list_html_at_path, login

log = logging.getLogger(__name__)

DEFAULT_EDU_LIST_PATH = (
    "/admin/edu/edu_list.html?select_key=&input_key=&search=&cate=&el_state=-1"
    "&el_area=&el_code=&el_startdate=&el_enddate=&page=1"
)


def _cell_text(cell) -> str:
    return re.sub(r"\s+", " ", cell.get_text(separator=" ", strip=True) or "").strip()


def _normalize_key(s: str) -> str:
    key = re.sub(r"\s+", "_", s.strip())
    key = re.sub(r"[^\w가-힣]+", "", key, flags=re.UNICODE)
    return key or "col"


def strip_nbsp(s: str) -> str:
    t = s.replace("\u00a0", " ").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t).strip()


def extract_leading_paren_unit(raw: str) -> str | None:
    """교육명 선두 (IECEx 010) → IECEx 010. title 은 셀 원문 유지."""
    s = strip_nbsp(raw)
    if not s:
        return None
    m = re.match(r"^\s*\(([^)]*)\)\s*", s)
    if not m:
        return None
    inner = strip_nbsp(m.group(1) or "")
    return inner or None


def extract_edu_row_seq(tr) -> str | None:
    """신청자 목록 URL은 el_seq=… 이므로, 행 HTML 안의 el_seq 를 체크박스보다 우선한다."""
    html = str(tr)
    el = re.search(r"[?&]el_seq=(\d+)", html, re.I)
    if el:
        return el.group(1)
    cb = tr.select_one('input[type="checkbox"][name="seq_list[]"]') or tr.select_one(
        'input[type="checkbox"][name^="seq_list"]'
    )
    if cb and cb.get("value"):
        v = str(cb["value"]).strip()
        if v.isdigit():
            return v
    seqs = re.findall(r"[?&]seq=(\d+)", html, re.I)
    if seqs:
        return Counter(seqs).most_common(1)[0][0]
    return None


def pick(row: dict[str, str], keys: list[str]) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def parse_edu_table_with_trs(
    html: str, table_selector: str
) -> tuple[list[str], list[tuple[object, dict[str, str]]], int]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one(table_selector)
    if table is None:
        table = soup.find("table")
    if table is None:
        raise RuntimeError("Could not find a <table>")

    rows = table.find_all("tr")
    if not rows:
        return [], [], 0

    header_cells = rows[0].find_all(["th", "td"])
    headers = [_normalize_key(_cell_text(c)) for c in header_cells]
    seen: dict[str, int] = {}
    unique_headers: list[str] = []
    for h in headers:
        n = seen.get(h, 0)
        seen[h] = n + 1
        unique_headers.append(h if n == 0 else f"{h}_{n + 1}")

    def _data_cell_offset(tr_el, header_len: int) -> int:
        cells = tr_el.find_all(["td", "th"])
        n = len(cells)
        if n <= header_len:
            return 0
        first = cells[0]
        has_cb = first.find("input", type="checkbox") is not None
        if has_cb and n == header_len + 1:
            return 1
        return 0

    data_rows = rows[1:]
    cell_offset = (
        _data_cell_offset(data_rows[0], len(unique_headers)) if data_rows else 0
    )

    pairs: list[tuple[object, dict[str, str]]] = []
    for tr in data_rows:
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        row: dict[str, str] = {}
        ext = extract_edu_row_seq(tr)
        if ext:
            row["_seq"] = ext
        for i, c in enumerate(cells):
            if i < cell_offset:
                continue
            hi = i - cell_offset
            key = unique_headers[hi] if hi < len(unique_headers) else f"col_{i}"
            row[key] = _cell_text(c)
        if any(v for v in row.values()):
            pairs.append((tr, row))

    return unique_headers, pairs, cell_offset


def row_to_edu_db_payload(row: dict[str, str]) -> dict[str, object]:
    seq_raw = (row.get("_seq") or "").strip()
    if not seq_raw or not seq_raw.isdigit():
        return {"_error": "no _seq", "row_keys": list(row.keys())}
    seq = int(seq_raw)

    def t(keys: list[str]) -> str | None:
        v = strip_nbsp(pick(row, keys))
        return v or None

    title_raw = pick(row, ["교육명", "제목", "강좌명", "title"])
    title = strip_nbsp(title_raw)
    unit = extract_leading_paren_unit(title_raw)
    return {
        "seq": seq,
        "display_no": t(["번호", "No", "no"]),
        "region": t(["지역", "region"]),
        "title": title or "[제목없음]",
        "unit": unit,
        "edu_period": t(["교육기간일시", "교육기간", "교육_기간"]),
        "apply_period": t(["접수기간", "접수_기간"]),
        "capacity": t(["정원", "모집인원", "capacity"]),
        "category": t(["분류", "카테고리", "category"]),
        "registered_at": t(["등록일자", "등록일", "created_at"]),
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    p = argparse.ArgumentParser(description="Fetch one edu_list page and print parsed JSON")
    p.add_argument("--page", type=int, default=1, help="list page number (default 1)")
    p.add_argument("--limit", type=int, default=0, help="max rows to print (0 = all)")
    args = p.parse_args()

    base = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    login_path = (os.getenv("LOGIN_PATH") or "").strip()
    admin_user = os.getenv("ADMIN_USER") or ""
    admin_pass = os.getenv("ADMIN_PASSWORD") or ""
    if not base or not login_path or not admin_user or not admin_pass:
        log.error(
            "Set BASE_URL, LOGIN_PATH, ADMIN_USER, ADMIN_PASSWORD in .env",
        )
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
        log.warning("LOGIN_EXTRA_FIELDS_JSON invalid; using {}")
        login_extra = {}

    list_base = (os.getenv("EDU_LIST_PATH") or DEFAULT_EDU_LIST_PATH).strip()
    table_sel = (os.getenv("TABLE_SELECTOR") or "table.list_table").strip()

    path = list_path_for_page(list_base, args.page)
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
    log.info("GET %s", path)
    html = fetch_list_html_at_path(session, settings, path)

    headers, pairs, cell_offset = parse_edu_table_with_trs(html, table_sel)
    out: dict[str, object] = {
        "page": args.page,
        "path": path,
        "header_keys": headers,
        "data_cell_offset": cell_offset,
        "row_count": len(pairs),
        "rows": [],
    }

    lim = args.limit if args.limit > 0 else len(pairs)
    for i, (tr, row) in enumerate(pairs[:lim]):
        ext = extract_edu_row_seq(tr)
        cb_val = None
        cb = tr.select_one('input[type="checkbox"][name^="seq_list"]')
        if cb and cb.get("value"):
            cb_val = str(cb["value"]).strip()
        upsert = row_to_edu_db_payload(row)
        out["rows"].append(
            {
                "index": i,
                "checkbox_value": cb_val,
                "extract_edu_row_seq": ext,
                "row__seq": row.get("_seq"),
                "seq_mismatch": (cb_val != ext) if (cb_val and ext) else None,
                "raw_cells": {
                    k: v
                    for k, v in row.items()
                    if not k.startswith("_") and k != "col"
                },
                "upsert_row": upsert,
            }
        )

    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
