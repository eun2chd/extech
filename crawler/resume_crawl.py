"""
관리자 이력서 목록/상세 크롤 → PostgreSQL (`crawl_*` 테이블, schema_resume.sql).

.env
  BASE_URL, LOGIN_PATH, ADMIN_USER, ADMIN_PASSWORD
  LOGIN_USER_FIELD / LOGIN_PASS_FIELD (ex-tech: m_id, m_pass)
  DATABASE_URL — postgresql://user:pass@host:5432/dbname
  RESUME_LIST_PATH — 기본 /admin/resume/resume_list.html?page=1
  RESUME_DETAIL_PATH_TEMPLATE — 기본 /admin/resume/resume_form.html?mode=modify&seq={seq}
  RESUME_LIST_DELAY_SECONDS — 목록 페이지 간 (기본 0.5)
  RESUME_LIST_HTTP_TIMEOUT — 목록 GET 읽기 타임아웃 초 (기본 120, crawler.session)
  RESUME_DETAIL_DELAY_SECONDS — 상세 요청 간 (기본 0.7)

동작: **LIST** — `crawl_resumes.seq`(외부 키)만으로 부모 행 INSERT(`ON CONFLICT DO NOTHING`). **DETAIL** — `resume_id`로 `crawl_resumes` 본문 UPDATE + `crawl_resume_*` 자식만 재삽입. 목록은 페이지 단위로 이어 처리.

실행:
  python -m crawler.resume_crawl
  python -m crawler.resume_crawl --check-db
  python -m crawler.resume_crawl --dry-run --max-pages 2
  python -m crawler.resume_crawl --start-page 97
  python -m crawler.resume_crawl --seq 12345
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from urllib.parse import urlparse
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

from bs4 import BeautifulSoup
from dotenv import load_dotenv

from crawler.list_pager import list_path_for_page
from crawler.session import build_session, fetch_list_html_at_path, login, resolve_url

log = logging.getLogger(__name__)


def _dsn_log_host(dsn: str) -> str:
    """비밀번호 없이 연결 대상만 로그용으로 표시."""
    s = (dsn or "").strip()
    if not s:
        return "(empty DATABASE_URL)"
    try:
        p = urlparse(s)
        host = p.hostname or "?"
        port = f":{p.port}" if p.port else ""
        user = p.username or ""
        db = (p.path or "").lstrip("/") or "?"
        return f"{p.scheme}://{user + '@' if user else ''}{host}{port}/{db}"
    except Exception:
        return "(unparseable DATABASE_URL)"


def _format_http_err(exc: BaseException, url: str | None = None) -> str:
    """requests/HTTP 계열 예외 요약 (상태 코드·URL)."""
    parts: list[str] = [f"{type(exc).__name__}: {exc!s}"]
    if url:
        parts.append(f"url={url!r}")
    resp = getattr(exc, "response", None)
    if resp is not None:
        parts.append(f"http_status={getattr(resp, 'status_code', None)!r}")
        ru = getattr(resp, "url", None)
        if ru:
            parts.append(f"response_url={ru!r}")
        if hasattr(resp, "headers"):
            ct = resp.headers.get("Content-Type", "")
            if ct:
                parts.append(f"content_type={ct!r}")
    return " | ".join(parts)


def _format_db_exception(exc: BaseException) -> str:
    """PostgreSQL / psycopg2 오류 한 줄 요약 (pgcode·diag·pgerror)."""
    parts: list[str] = [f"{type(exc).__name__}: {exc!s}"]
    has_pg = (
        getattr(exc, "pgcode", None) is not None
        or getattr(exc, "diag", None) is not None
        or getattr(exc, "pgerror", None) is not None
    )
    if not has_pg:
        return parts[0]
    pgcode = getattr(exc, "pgcode", None)
    if pgcode is not None:
        parts.append(f"pgcode={pgcode!r}")
    pgerr = getattr(exc, "pgerror", None)
    if pgerr and str(pgerr).strip():
        parts.append(f"pgerror={str(pgerr).strip()!r}")
    diag = getattr(exc, "diag", None)
    if diag is not None:
        for name in (
            "severity",
            "sqlstate",
            "schema_name",
            "table_name",
            "column_name",
            "constraint_name",
            "datatype_name",
            "message_primary",
            "message_detail",
            "message_hint",
        ):
            try:
                val = getattr(diag, name)
            except Exception:
                val = None
            if val not in (None, ""):
                parts.append(f"{name}={val!r}")
    return " | ".join(parts)


DEFAULT_RESUME_LIST = "/admin/resume/resume_list.html?page=1"
DEFAULT_DETAIL_TMPL = "/admin/resume/resume_form.html?mode=modify&seq={seq}"

# public 크롤 전용 테이블 (schema_resume.sql)
T_CRAWL_RESUMES = "crawl_resumes"
T_CRAWL_RESUME_DETAILS = "crawl_resume_details"
T_CRAWL_RESUME_EDUCATIONS = "crawl_resume_educations"
T_CRAWL_RESUME_CAREERS = "crawl_resume_careers"
T_CRAWL_RESUME_PROJECTS = "crawl_resume_projects"
T_CRAWL_RESUME_TRAININGS = "crawl_resume_trainings"
T_CRAWL_RESUME_CERTIFICATES = "crawl_resume_certificates"
T_CRAWL_RESUME_IECEX = "crawl_resume_iecex"

CRAWL_RESUME_TABLE_NAMES = (
    T_CRAWL_RESUMES,
    T_CRAWL_RESUME_DETAILS,
    T_CRAWL_RESUME_EDUCATIONS,
    T_CRAWL_RESUME_CAREERS,
    T_CRAWL_RESUME_PROJECTS,
    T_CRAWL_RESUME_TRAININGS,
    T_CRAWL_RESUME_CERTIFICATES,
    T_CRAWL_RESUME_IECEX,
)

CRAWL_RESUME_CHILD_TABLES_DELETE_ORDER = (
    T_CRAWL_RESUME_IECEX,
    T_CRAWL_RESUME_CERTIFICATES,
    T_CRAWL_RESUME_TRAININGS,
    T_CRAWL_RESUME_PROJECTS,
    T_CRAWL_RESUME_CAREERS,
    T_CRAWL_RESUME_EDUCATIONS,
    T_CRAWL_RESUME_DETAILS,
)

_DATE_PATTERNS = (
    re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"),
    re.compile(r"(\d{4})(\d{2})(\d{2})"),
)


def _session_settings_from_env() -> SimpleNamespace | None:
    base = (os.getenv("BASE_URL") or "").strip().rstrip("/")
    login_path = (os.getenv("LOGIN_PATH") or "").strip()
    admin_user = os.getenv("ADMIN_USER") or ""
    admin_pass = os.getenv("ADMIN_PASSWORD") or ""
    if not base or not login_path or not admin_user or not admin_pass:
        return None
    user_f = os.getenv("LOGIN_USER_FIELD", "m_id") or "m_id"
    pass_f = os.getenv("LOGIN_PASS_FIELD", "m_pass") or "m_pass"
    verify_tls = (os.getenv("VERIFY_TLS", "true") or "true").lower() in (
        "1",
        "true",
        "yes",
    )
    extra_raw = os.getenv("LOGIN_EXTRA_FIELDS_JSON", "{}") or "{}"
    try:
        import json as _json

        login_extra = _json.loads(extra_raw)
        if not isinstance(login_extra, dict):
            login_extra = {}
    except Exception:
        login_extra = {}
    return SimpleNamespace(
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


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name) or str(default)))
    except ValueError:
        return default


def parse_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    s = re.sub(r"\s+", " ", str(raw).strip())
    if not s:
        return None
    for pat in _DATE_PATTERNS:
        m = pat.search(s)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                try:
                    return date(y, mo, d)
                except ValueError:
                    return None
    return None


def _clean_val(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _field_value(block: BeautifulSoup, tag: str, name: str) -> str | None:
    el = block.select_one(f'{tag}[name="{name}"]')
    if not el:
        return None
    if tag == "input":
        t = (el.get("type") or "").lower()
        if t == "checkbox":
            if not el.get("checked"):
                return None
            return _clean_val(el.get("value") or "on")
        return _clean_val(el.get("value"))
    if tag == "textarea":
        return _clean_val(el.string or el.get_text())
    if tag == "select":
        so = el.select_one("option[selected]")
        if so and so.get("value") is not None:
            return _clean_val(so.get("value"))
        if so:
            return _clean_val(so.get_text())
        so2 = el.select_one("option:checked") or el.find("option", selected=True)
        if so2:
            return _clean_val(so2.get("value") or so2.get_text())
        return None
    return None


def _input_or_select(block: BeautifulSoup, name: str) -> str | None:
    for tag in ("input", "select", "textarea"):
        v = _field_value(block, tag, name)
        if v is not None:
            return v
    return None


def collect_named_fields(block: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    for el in block.select("input[name], select[name], textarea[name]"):
        nm = el.get("name")
        if not nm:
            continue
        key = str(nm).strip()
        if not key:
            continue
        tag = el.name.lower()
        v = _field_value(block, tag, key)
        if v is not None:
            out[key] = v
    return out


def _lang_level(soup: BeautifulSoup, idx: int) -> str | None:
    for name in (
        f"rl_lng{idx}_level",
        f"rl_lng{idx}_lvl",
        f"lng{idx}_level",
        f"rl_level{idx}",
    ):
        v = _input_or_select(soup, name)
        if v:
            return v
    return None


def parse_resume_list_tr(tr: Any) -> dict[str, Any] | None:
    """
    tr.cont 한 줄에서 seq·번호·각 td 텍스트·(옵션) 목록상 아이디·이름 추출.
    td 인덱스는 0부터. 기본: 1=번호, 2=이름, 3=아이디 (RESUME_LIST_NAME_TD_INDEX 등으로 변경).
    """
    tds = tr.find_all("td", recursive=False)
    if len(tds) < 2:
        return None
    cells = [
        re.sub(r"\s+", " ", (td.get_text(" ", strip=True) or "").strip())
        for td in tds
    ]
    cb = tr.select_one('input[name="seq_list[]"]') or tr.select_one(
        'input[type="checkbox"][name^="seq_list"]',
    )
    if not cb or not cb.get("value"):
        return None
    vs = str(cb["value"]).strip()
    if not vs.isdigit():
        return None
    seq = vs
    num_raw = cells[1] if len(cells) > 1 else ""
    name_i = int(os.getenv("RESUME_LIST_NAME_TD_INDEX", "2") or "2")
    uid_i = int(os.getenv("RESUME_LIST_USER_ID_TD_INDEX", "3") or "3")

    def cell(i: int) -> str | None:
        if 0 <= i < len(cells) and cells[i]:
            return cells[i]
        return None

    return {
        "seq": seq,
        "list_no": num_raw,
        "cells": cells,
        "user_id_from_list": cell(uid_i),
        "name_from_list": cell(name_i),
    }


def list_row_stub(seq: int | str) -> dict[str, Any]:
    """--seq 전용: 목록 행 없이 상세만 돌릴 때 최소 행."""
    return {
        "seq": seq,
        "list_no": "",
        "cells": [],
        "user_id_from_list": None,
        "name_from_list": None,
    }


def iter_resume_list_seq_batches(
    session: Any,
    settings: SimpleNamespace,
    list_path_base: str | None = None,
    list_delay_s: float = 0.5,
    max_pages: int = 0,
    start_page: int = 1,
):
    """
    목록 페이지마다 (page, [(seq, list_row_dict), ...], 마지막 페이지 여부) 를 넘김.
    list_row_dict 는 parse_resume_list_tr 결과 — DB 목록 1차 INSERT 에 사용.
    start_page: 첫 요청 page= 값 (이전에 끊긴 페이지부터 이어하기).
    max_pages>0 이면 page 가 이 값 이상이면 종료 (절대 페이지 번호 상한).
    """
    base = (list_path_base or os.getenv("RESUME_LIST_PATH") or DEFAULT_RESUME_LIST).strip()
    seen: set[str] = set()
    page = max(1, int(start_page))
    if page > 1:
        log.info("resume list starting at page=%s (pages before this are skipped)", page)
    while True:
        path = list_path_for_page(base, page)
        log.info("resume list page=%s path=%s", page, path)
        try:
            html = fetch_list_html_at_path(session, settings, path)
        except Exception as e:
            url = resolve_url(settings.base_url, path)
            log.error(
                "list fetch failed page=%s: %s",
                page,
                _format_http_err(e, url),
            )
            log.exception("list fetch traceback page=%s", page)
            raise
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("tr.cont")
        if not rows:
            log.warning("no tr.cont on page %s; stopping", page)
            break
        page_has_one = False
        page_batch: list[tuple[str, dict[str, Any]]] = []
        for tr in rows:
            parsed = parse_resume_list_tr(tr)
            if not parsed:
                continue
            if parsed.get("list_no") == "1":
                page_has_one = True
            seq = parsed["seq"]
            if seq not in seen:
                seen.add(seq)
                page_batch.append((seq, parsed))
        log.info(
            "resume list page=%s → %s new seq(s) on this page (unique total %s)",
            page,
            len(page_batch),
            len(seen),
        )
        yield page, page_batch, page_has_one
        if page_has_one:
            log.info("list page %s contains row number 1 — last page", page)
            break
        if max_pages > 0 and page >= max_pages:
            log.info("max_pages=%s reached (stop without row number 1)", max_pages)
            break
        page += 1
        if list_delay_s > 0:
            time.sleep(list_delay_s)
    log.info("list crawl finished; %s unique seq(s) seen", len(seen))


def get_seq_list(
    session: Any,
    settings: SimpleNamespace,
    list_path_base: str | None = None,
    list_delay_s: float = 0.5,
    max_pages: int = 0,
    start_page: int = 1,
) -> list[str]:
    """
    resume_list.html?page=N 순회.
    tr.cont 마다 seq_list[] value 와 두 번째 td(번호) 수집.
    번호가 '1'인 행이 있으면 해당 페이지까지 수집 후 종료.
    """
    ordered: list[str] = []
    for _, batch, _ in iter_resume_list_seq_batches(
        session,
        settings,
        list_path_base=list_path_base,
        list_delay_s=list_delay_s,
        max_pages=max_pages,
        start_page=start_page,
    ):
        ordered.extend(seq for seq, _ in batch)
    log.info("collected %s unique seq(s)", len(ordered))
    return ordered


def _detail_path(seq: int | str) -> str:
    tmpl = (
        os.getenv("RESUME_DETAIL_PATH_TEMPLATE") or DEFAULT_DETAIL_TMPL
    ).strip()
    return tmpl.replace("{seq}", str(seq))


def fetch_detail_html(session: Any, settings: SimpleNamespace, seq: int | str) -> str:
    path = _detail_path(seq)
    url = resolve_url(settings.base_url, path)
    log.info("GET resume detail seq=%s url=%s", seq, url)
    try:
        r = session.get(url, timeout=120)
        r.raise_for_status()
    except Exception as e:
        log.error(
            "fetch_detail_html failed seq=%s: %s",
            seq,
            _format_http_err(e, url),
        )
        raise
    r.encoding = r.apparent_encoding or r.encoding or "utf-8"
    return r.text


def _basic_from_soup(soup: BeautifulSoup) -> dict[str, Any]:
    def iv(name: str) -> str | None:
        return _input_or_select(soup, name)

    first = iv("rl_firstname")
    last = iv("rl_lastname")
    name_parts = [p for p in (first, last) if p]
    full_name = "".join(name_parts) if name_parts else None

    sel = soup.select_one('select[name="rl_country_code"]')
    country_code = None
    country_name = None
    if sel:
        opt = sel.select_one("option[selected]") or sel.find("option", selected=True)
        if opt:
            country_code = _clean_val(opt.get("value"))
            country_name = _clean_val(opt.get_text())
        if not country_code:
            opt2 = sel.select_one("option:checked")
            if opt2:
                country_code = _clean_val(opt2.get("value"))
                country_name = country_name or _clean_val(opt2.get_text())

    birth_raw = iv("rl_birth")
    birth_d = parse_date(birth_raw) if birth_raw else None

    return {
        "user_id": iv("rl_userid"),
        "name": full_name,
        "first_name": first,
        "last_name": last,
        "en_first_name": iv("rl_enfname"),
        "en_last_name": iv("rl_enlname"),
        "birth": birth_d.isoformat() if birth_d else None,
        "birth_date": birth_d,
        "country_code": country_code,
        "country_name": country_name,
    }


def _details_from_soup(soup: BeautifulSoup) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i in (1, 2, 3):
        lng = _input_or_select(soup, f"rl_lng{i}")
        lvl = _lang_level(soup, i)
        if lng:
            out[f"lang{i}"] = lng
        if lvl:
            out[f"lang{i}_level"] = lvl
    return out


def _append_education(block: BeautifulSoup, out: list[dict[str, Any]]) -> None:
    sch = _input_or_select(block, "rebl_schname")
    if not sch:
        return
    row: dict[str, Any] = {"school_name": sch}
    for k, name in (
        ("major", "rebl_major"),
        ("degree", "rebl_degree"),
        ("final_education", "rebl_final_education"),
    ):
        v = _input_or_select(block, name)
        if v:
            row[k] = v
    gr = _input_or_select(block, "rebl_graduation")
    if gr:
        d = parse_date(gr)
        row["graduation"] = d.isoformat() if d else gr
        row["graduation_date"] = d
    out.append(row)


def _append_career(block: BeautifulSoup, out: list[dict[str, Any]]) -> None:
    co = _input_or_select(block, "rph_company_name")
    if not co:
        return
    row: dict[str, Any] = {"company_name": co}
    for k, name, as_date in (
        ("start_date", "rph_startdate", True),
        ("end_date", "rph_enddate", True),
        ("department_name", "rph_company_dep_name", False),
        ("rank", "rph_rank", False),
        ("duty", "rph_duty", False),
        ("job_code", "rph_job_code", False),
    ):
        v = _input_or_select(block, name)
        if not v:
            continue
        if as_date:
            d = parse_date(v)
            row[k] = d.isoformat() if d else v
            row[f"{k}_date"] = d
        else:
            row[k] = v
    out.append(row)


def _append_project(block: BeautifulSoup, out: list[dict[str, Any]]) -> None:
    co = _input_or_select(block, "rpbl_company_name")
    if not co:
        return
    row: dict[str, Any] = {"company_name": co}
    for k, name, as_date in (
        ("start_date", "rpbl_company_startdate", True),
        ("end_date", "rpbl_company_enddate", True),
    ):
        v = _input_or_select(block, name)
        if not v:
            continue
        d = parse_date(v)
        row[k] = d.isoformat() if d else v
        row[f"{k}_date"] = d
    for k, name in (
        ("duty", "rpbl_company_duty"),
        ("memo", "rpbl_company_memo"),
    ):
        v = _input_or_select(block, name)
        if v:
            row[k] = v
    out.append(row)


def _append_training(block: BeautifulSoup, out: list[dict[str, Any]]) -> None:
    nm = _input_or_select(block, "rtl_name")
    if not nm:
        return
    row: dict[str, Any] = {"name": nm}
    for k, name in (("center", "rtl_center"), ("memo", "rtl_memo")):
        v = _input_or_select(block, name)
        if v:
            row[k] = v
    for k, name in (("start_date", "rtl_startdate"), ("end_date", "rtl_enddate")):
        v = _input_or_select(block, name)
        if not v:
            continue
        d = parse_date(v)
        row[k] = d.isoformat() if d else v
        row[f"{k}_date"] = d
    out.append(row)


def _append_certificate(block: BeautifulSoup, out: list[dict[str, Any]]) -> None:
    nm = _input_or_select(block, "rcl_name")
    if not nm:
        return
    row: dict[str, Any] = {"name": nm}
    pub = _input_or_select(block, "rcl_publish")
    if pub:
        row["publisher"] = pub
    dt = _input_or_select(block, "rcl_date")
    if dt:
        d = parse_date(dt)
        row["issue_date"] = d.isoformat() if d else dt
        row["issue_date_date"] = d
    out.append(row)


def _append_iecex(block: BeautifulSoup, out: list[dict[str, Any]]) -> None:
    code = _input_or_select(block, "rcl_iece_code")
    if not code:
        return
    row: dict[str, Any] = {"iece_code": code}
    pc = _input_or_select(block, "rcl_iece_pcode")
    if pc:
        row["iece_pcode"] = pc
    dt = _input_or_select(block, "rcl_iece_date")
    if dt:
        d = parse_date(dt)
        row["iece_date"] = d.isoformat() if d else dt
        row["iece_date_date"] = d
    out.append(row)


def _process_data_type_block(
    dtype: str,
    block: BeautifulSoup,
    buckets: dict[str, list[Any]],
) -> None:
    dtype = (dtype or "").strip()
    fields = collect_named_fields(block)
    if fields.get("rcl_iece_code"):
        _append_iecex(block, buckets["iecex"])
        return
    if dtype == "학력" or (dtype == "" and fields.get("rebl_schname")):
        _append_education(block, buckets["educations"])
        return
    if dtype == "경력" or (dtype == "" and fields.get("rph_company_name")):
        _append_career(block, buckets["careers"])
        return
    if dtype == "전문경력" or (dtype == "" and fields.get("rpbl_company_name")):
        _append_project(block, buckets["projects"])
        return
    if dtype in ("훈련이수", "교육") or (dtype == "" and fields.get("rtl_name")):
        _append_training(block, buckets["trainings"])
        return
    if fields.get("rcl_name") and not fields.get("rcl_iece_code"):
        _append_certificate(block, buckets["certificates"])
        return
    if "iece" in dtype.lower() or "iecex" in dtype.lower():
        _append_iecex(block, buckets["iecex"])


def _strip_internal_dates(row: dict[str, Any]) -> dict[str, Any]:
    """JSON 직렬화용: *_date 내부 키 제거 (insert_db는 원본 dict 별도 처리)."""
    return {k: v for k, v in row.items() if not k.endswith("_date") or k in ("birth",)}


def get_detail_for_insert(
    session: Any, settings: SimpleNamespace, seq: int | str
) -> dict[str, Any]:
    """insert_db용: 내부 date 객체 포함."""
    seq_s = str(seq).strip()
    html = fetch_detail_html(session, settings, seq)
    try:
        soup = BeautifulSoup(html, "html.parser")
        basic = _basic_from_soup(soup)
        details = _details_from_soup(soup)
        buckets: dict[str, list[Any]] = {
            "educations": [],
            "careers": [],
            "projects": [],
            "trainings": [],
            "certificates": [],
            "iecex": [],
        }
        for block in soup.find_all(attrs={"data-type": True}):
            _process_data_type_block(str(block.get("data-type") or ""), block, buckets)
        if not any(buckets.values()):
            for block in soup.select("tr.cont, tr.resume_row, div.resume_block"):
                dtype = str(block.get("data-type") or "")
                flds = collect_named_fields(block)
                if not flds:
                    continue
                _process_data_type_block(dtype, block, buckets)
        return {
            "seq": seq_s,
            "basic": basic,
            "details": details,
            "educations": buckets["educations"],
            "careers": buckets["careers"],
            "projects": buckets["projects"],
            "trainings": buckets["trainings"],
            "certificates": buckets["certificates"],
            "iecex": buckets["iecex"],
        }
    except Exception as e:
        log.error(
            "get_detail_for_insert parse/extract failed seq=%s html_len=%s: %s",
            seq_s,
            len(html or ""),
            f"{type(e).__name__}: {e!s}",
        )
        raise


def serialize_resume_payload(data: dict[str, Any]) -> dict[str, Any]:
    """DB/내부용 payload → JSON 출력용 (birth_date·*_date 제거)."""
    basic = {k: v for k, v in data["basic"].items() if k != "birth_date"}
    return {
        "seq": data["seq"],
        "basic": basic,
        "details": dict(data["details"]),
        "educations": [_strip_internal_dates(x) for x in data["educations"]],
        "careers": [_strip_internal_dates(x) for x in data["careers"]],
        "projects": [_strip_internal_dates(x) for x in data["projects"]],
        "trainings": [_strip_internal_dates(x) for x in data["trainings"]],
        "certificates": [_strip_internal_dates(x) for x in data["certificates"]],
        "iecex": [_strip_internal_dates(x) for x in data["iecex"]],
    }


def get_detail(session: Any, settings: SimpleNamespace, seq: int | str) -> dict[str, Any]:
    """상세 1건 — 직렬화된 구조 (요구사항 JSON 스키마)."""
    return serialize_resume_payload(get_detail_for_insert(session, settings, seq))


def get_resume_id(conn: Any, seq: int | str) -> int:
    """`crawl_resumes.seq` 로 내부 PK `id` 조회."""
    seq_s = str(seq).strip()
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT id FROM {T_CRAWL_RESUMES} WHERE seq = %s",
            (seq_s,),
        )
        r = cur.fetchone()
        if not r:
            raise RuntimeError(f"no resume row for seq={seq_s!r}")
        return int(r[0])
    finally:
        cur.close()


def insert_resume_from_list(conn: Any, list_row: dict[str, Any]) -> int:
    """
    LIST 단계: `crawl_resumes(seq, …)` 부모만 생성. 동일 seq 이면 `DO NOTHING` 후 기존 id 반환.
    커밋은 호출하지 않음 — merge_resume_detail 과 한 트랜잭션으로 묶는다.
    """
    from psycopg2.extras import Json

    seq_s = str(list_row["seq"]).strip()
    list_no = list_row.get("list_no")
    if list_no is not None:
        list_no = str(list_no).strip() or None
    cells = list_row.get("cells") or []
    extra = {
        k: v
        for k, v in (
            ("user_id_from_list", list_row.get("user_id_from_list")),
            ("name_from_list", list_row.get("name_from_list")),
        )
        if v
    }
    row_json = None
    if cells or list_no or extra:
        payload: dict[str, Any] = {"cells": cells}
        if list_no:
            payload["list_no"] = list_no
        payload.update(extra)
        row_json = Json(payload)

    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            INSERT INTO {T_CRAWL_RESUMES} (seq, list_no, list_row_json)
            VALUES (%s, %s, %s)
            ON CONFLICT (seq) DO NOTHING
            RETURNING id
            """,
            (seq_s, list_no, row_json),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
        cur.execute(
            f"SELECT id FROM {T_CRAWL_RESUMES} WHERE seq = %s",
            (seq_s,),
        )
        row2 = cur.fetchone()
        if not row2:
            raise RuntimeError(f"resume seq={seq_s!r} missing after LIST insert")
        return int(row2[0])
    finally:
        cur.close()


def merge_resume_detail(conn: Any, resume_id: int, data: dict[str, Any]) -> None:
    """
    DETAIL 단계: LIST에서 만든 `resume_id` 행에 폼 기본 필드 UPDATE + 1:N 자식 전부 삭제 후 재삽입.
    data: get_detail_for_insert 결과.
    """
    basic = data["basic"]
    details = data["details"]
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE {T_CRAWL_RESUMES} SET
                user_id = %s,
                name = %s,
                first_name = %s,
                last_name = %s,
                en_first_name = %s,
                en_last_name = %s,
                birth = %s,
                country_code = %s,
                country_name = %s
            WHERE id = %s
            """,
            (
                basic.get("user_id"),
                basic.get("name"),
                basic.get("first_name"),
                basic.get("last_name"),
                basic.get("en_first_name"),
                basic.get("en_last_name"),
                basic.get("birth_date"),
                basic.get("country_code"),
                basic.get("country_name"),
                resume_id,
            ),
        )
        for tbl in CRAWL_RESUME_CHILD_TABLES_DELETE_ORDER:
            cur.execute(f"DELETE FROM {tbl} WHERE resume_id = %s", (resume_id,))

        cur.execute(
            f"""
            INSERT INTO {T_CRAWL_RESUME_DETAILS} (
                resume_id, lang1, lang1_level, lang2, lang2_level, lang3, lang3_level
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                resume_id,
                details.get("lang1"),
                details.get("lang1_level"),
                details.get("lang2"),
                details.get("lang2_level"),
                details.get("lang3"),
                details.get("lang3_level"),
            ),
        )

        for row in data.get("educations") or []:
            cur.execute(
                f"""
                INSERT INTO {T_CRAWL_RESUME_EDUCATIONS} (
                    resume_id, school_name, major, degree, final_education, graduation
                ) VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    resume_id,
                    row.get("school_name"),
                    row.get("major"),
                    row.get("degree"),
                    row.get("final_education"),
                    row.get("graduation_date")
                    or parse_date(str(row.get("graduation") or "")),
                ),
            )

        for row in data.get("careers") or []:
            cur.execute(
                f"""
                INSERT INTO {T_CRAWL_RESUME_CAREERS} (
                    resume_id, company_name, start_date, end_date,
                    department_name, rank_title, duty, job_code
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    resume_id,
                    row.get("company_name"),
                    row.get("start_date_date")
                    or parse_date(str(row.get("start_date") or "")),
                    row.get("end_date_date")
                    or parse_date(str(row.get("end_date") or "")),
                    row.get("department_name"),
                    row.get("rank"),
                    row.get("duty"),
                    row.get("job_code"),
                ),
            )

        for row in data.get("projects") or []:
            cur.execute(
                f"""
                INSERT INTO {T_CRAWL_RESUME_PROJECTS} (
                    resume_id, company_name, start_date, end_date, duty, memo
                ) VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    resume_id,
                    row.get("company_name"),
                    row.get("start_date_date")
                    or parse_date(str(row.get("start_date") or "")),
                    row.get("end_date_date")
                    or parse_date(str(row.get("end_date") or "")),
                    row.get("duty"),
                    row.get("memo"),
                ),
            )

        for row in data.get("trainings") or []:
            cur.execute(
                f"""
                INSERT INTO {T_CRAWL_RESUME_TRAININGS} (
                    resume_id, name, center, start_date, end_date, memo
                ) VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (
                    resume_id,
                    row.get("name"),
                    row.get("center"),
                    row.get("start_date_date")
                    or parse_date(str(row.get("start_date") or "")),
                    row.get("end_date_date")
                    or parse_date(str(row.get("end_date") or "")),
                    row.get("memo"),
                ),
            )

        for row in data.get("certificates") or []:
            cur.execute(
                f"""
                INSERT INTO {T_CRAWL_RESUME_CERTIFICATES} (
                    resume_id, name, publisher, issue_date
                ) VALUES (%s,%s,%s,%s)
                """,
                (
                    resume_id,
                    row.get("name"),
                    row.get("publisher"),
                    row.get("issue_date_date")
                    or parse_date(str(row.get("issue_date") or "")),
                ),
            )

        for row in data.get("iecex") or []:
            cur.execute(
                f"""
                INSERT INTO {T_CRAWL_RESUME_IECEX} (
                    resume_id, iece_code, iece_pcode, iece_date
                ) VALUES (%s,%s,%s,%s)
                """,
                (
                    resume_id,
                    row.get("iece_code"),
                    row.get("iece_pcode"),
                    row.get("iece_date_date")
                    or parse_date(str(row.get("iece_date") or "")),
                ),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def insert_db(conn: Any, data: dict[str, Any]) -> int:
    """
    목록 없이 상세만 넣을 때(스크립트용): stub 행 후 머지.
    returns crawl_resumes.id
    """
    seq = data["seq"]
    try:
        rid = insert_resume_from_list(conn, list_row_stub(seq))
        merge_resume_detail(conn, rid, data)
        return rid
    except Exception as e:
        log.error(
            "insert_db failed seq=%r: %s",
            seq,
            _format_db_exception(e),
        )
        raise


def check_resume_db() -> int:
    """DATABASE_URL 로만 접속·간단 쿼리·이력서 테이블 존재 여부 확인. 크롤/로그인 없음."""
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        log.error("Set DATABASE_URL in .env")
        return 1
    try:
        import psycopg2
    except ImportError:
        log.error("Install PostgreSQL driver: pip install psycopg2-binary")
        return 1
    try:
        conn = psycopg2.connect(dsn)
    except Exception as e:
        log.error(
            "connect failed target=%s: %s",
            _dsn_log_host(dsn),
            _format_db_exception(e),
        )
        log.exception("connect traceback")
        return 1
    try:
        cur = conn.cursor()
        cur.execute("SELECT current_database(), current_user, version()")
        dbname, user, ver = cur.fetchone()
        log.info("connected: database=%r user=%r", dbname, user)
        log.info("server: %s", (ver or "")[:120])
        cur.execute("SELECT 1 AS ok")
        one = cur.fetchone()[0]
        if one != 1:
            log.error("unexpected SELECT 1 result: %s", one)
            return 1
        for t in CRAWL_RESUME_TABLE_NAMES:
            cur.execute(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = %s
                )
                """,
                (t,),
            )
            exists = cur.fetchone()[0]
            if exists:
                log.info("table public.%s: OK", t)
            else:
                log.warning("table public.%s: missing (run schema_resume.sql)", t)
        cur.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = 'seq'
            """,
            (T_CRAWL_RESUMES,),
        )
        if not cur.fetchone():
            log.warning(
                "column public.%s.seq missing — run schema_resume_migrate.sql or schema_resume_drop.sql + schema_resume.sql",
                T_CRAWL_RESUMES,
            )
        cur.execute(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s AND column_name = 'user_id'
            """,
            (T_CRAWL_RESUMES,),
        )
        ut = cur.fetchone()
        if ut and str(ut[0]).lower() == "uuid":
            log.warning(
                "public.%s.user_id is UUID — 폼의 사이트 아이디(숫자·문자)를 넣을 수 없어 "
                "invalid input syntax for type uuid 가 납니다. schema_resume_migrate.sql 맨 위 DO 블록으로 "
                "VARCHAR(255)로 바꾸세요. user_id에 FK가 있으면 먼저 제거해야 할 수 있습니다.",
                T_CRAWL_RESUMES,
            )
        cur.close()
        log.info("DB check finished successfully")
        return 0
    except Exception as e:
        log.error("DB check query failed: %s", _format_db_exception(e))
        log.exception("DB check traceback")
        return 1
    finally:
        conn.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()
    ap = argparse.ArgumentParser(description="Resume list/detail crawl → PostgreSQL")
    ap.add_argument(
        "--check-db",
        action="store_true",
        help="DATABASE_URL 접속만 검증 (로그인·목록·상세 없음)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="DB 없이 seq 수집 + 첫 상세 JSON stdout",
    )
    ap.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="목록 절대 page 상한 (0=두 번째 td 번호 '1' 행 나올 때까지). 예: --start-page 97 --max-pages 97 은 97페이지만",
    )
    ap.add_argument(
        "--start-page",
        type=int,
        default=1,
        metavar="N",
        help="목록 첫 요청 page=N (기본 1). 중단 후 이어하기: --start-page 97",
    )
    ap.add_argument(
        "--seq",
        action="append",
        dest="seqs",
        metavar="SEQ",
        help="지정 seq만 상세 수집 (여러 번 지정 가능). 목록 순회 생략",
    )
    args = ap.parse_args()

    if args.check_db:
        return check_resume_db()

    st = _session_settings_from_env()
    if not st:
        log.error("Set BASE_URL, LOGIN_PATH, ADMIN_USER, ADMIN_PASSWORD in .env")
        return 1

    if not args.dry_run:
        dsn_early = (os.getenv("DATABASE_URL") or "").strip()
        if not dsn_early:
            log.error(
                "Set DATABASE_URL in .env before crawling (or use --dry-run). "
                "Otherwise the list finishes and then fails with no DB URL.",
            )
            return 1
        try:
            import psycopg2  # noqa: F401
        except ImportError:
            log.error("Install PostgreSQL driver: pip install psycopg2-binary")
            return 1

    list_delay = _float_env("RESUME_LIST_DELAY_SECONDS", 0.5)
    detail_delay = _float_env("RESUME_DETAIL_DELAY_SECONDS", 0.7)

    session = build_session(st)
    try:
        login(session, st)
    except Exception as e:
        log.error("login failed: %s", _format_http_err(e))
        log.exception("login traceback")
        return 1

    dsn = (os.getenv("DATABASE_URL") or "").strip()
    conn = None
    if not args.dry_run:
        try:
            import psycopg2

            conn = psycopg2.connect(dsn)
        except Exception as e:
            log.error(
                "postgresql connect failed target=%s: %s",
                _dsn_log_host(dsn),
                _format_db_exception(e),
            )
            log.exception("postgresql connect traceback")
            return 1

    def _process_one_seq(
        seq: int | str,
        detail_index: int,
        list_row: dict[str, Any] | None,
    ) -> None:
        try:
            payload = get_detail_for_insert(session, st, seq)
        except Exception as e:
            mod = getattr(type(e), "__module__", "") or ""
            extra = (
                _format_http_err(e)
                if mod.startswith(("requests", "urllib3", "http"))
                else f"{type(e).__name__}: {e!s}"
            )
            log.error("get_detail_for_insert failed seq=%s: %s", seq, extra)
            log.exception("get_detail traceback seq=%s", seq)
            return
        if args.dry_run and detail_index == 0:
            json.dump(
                serialize_resume_payload(payload),
                sys.stdout,
                ensure_ascii=False,
                indent=2,
            )
            print()
        if conn:
            rid: int | None = None
            step = "LIST_insert_crawl_resumes"
            try:
                lr = list_row if list_row is not None else list_row_stub(seq)
                rid = insert_resume_from_list(conn, lr)
                step = "DETAIL_merge_resume_detail"
                merge_resume_detail(conn, rid, payload)
                log.info("list row + detail saved resume id=%s seq=%s", rid, seq)
            except Exception as e:
                # 첫 SQL 오류 후 트랜잭션이 aborted 상태로 남으면 다음 seq 전부 실패하므로 반드시 롤백
                try:
                    conn.rollback()
                except Exception:
                    log.exception("rollback after resume DB error failed seq=%s", seq)
                log.error(
                    "resume DB pipeline failed step=%s seq=%s resume_id=%s: %s",
                    step,
                    seq,
                    rid,
                    _format_db_exception(e),
                )
                log.exception("resume DB pipeline traceback seq=%s step=%s", seq, step)

    try:
        if args.seqs:
            seqs = [str(x).strip() for x in args.seqs if str(x).strip()]
            if not seqs:
                log.error("no seq to process")
                return 1
            for i, seq in enumerate(seqs):
                _process_one_seq(seq, i, None)
                if detail_delay > 0 and i + 1 < len(seqs):
                    time.sleep(detail_delay)
        else:
            detail_index = 0
            try:
                for page, batch, is_last_page in iter_resume_list_seq_batches(
                    session,
                    st,
                    list_delay_s=list_delay,
                    max_pages=args.max_pages,
                    start_page=max(1, args.start_page),
                ):
                    for j, (seq, lr) in enumerate(batch):
                        _process_one_seq(seq, detail_index, lr)
                        detail_index += 1
                        is_last_seq = is_last_page and (j == len(batch) - 1)
                        if detail_delay > 0 and not is_last_seq:
                            time.sleep(detail_delay)
            except Exception as e:
                log.error(
                    "resume list pipeline failed: %s",
                    f"{type(e).__name__}: {e!s}",
                )
                log.exception("resume list pipeline traceback")
                return 1
            if detail_index == 0:
                log.error("no seq to process")
                return 1
    finally:
        if conn:
            conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
