"""
교육 신청자 목록 크롤 → `upsert_edu_applicant_batch` RPC.

- 로컬 기본: `legacy_edu` 에서 seq·display_no 를 읽어 display_no 순으로 각 seq 의 신청 목록 전부 저장
- `--applicants-progress-mode`: Edge 와 동일 `edu_applicant_crawl_progress` + 한 스텝씩
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from supabase import Client

from crawler.edu_list_debug import parse_edu_table_with_trs, pick, strip_nbsp
from crawler.session import fetch_list_html_at_path, resolve_url
from crawler.store_edu import upsert_edu_applicant_batch

log = logging.getLogger(__name__)

APPLICANT_PROGRESS_ID = "default"

DEFAULT_EDU_APPLY_TEMPLATE = (
    "/admin/edu/edu_apply_list.html?el_seq={el_seq}"
)

# 헤더 normalize 시 공백·슬래시가 `_` 로 합쳐지면 ID__이력서보기 등이 됨 (edu_list_debug._normalize_key)
_APPLICANT_USER_ID_KEYS = [
    "ID이력서보기",
    "ID_이력서보기",
    "ID__이력서보기",
    "ID이력서",
    # <th>ID(이력서보기)</th> → normalize 시 괄호 제거
    "아이디",
    "회원아이디",
    "user_id",
    "USER_ID",
]

_BRACKET_NOISE = re.compile(r"\[[^\]]*\]|\([^)]*\)")


def _clean_user_id_cell(raw: str) -> str:
    t = strip_nbsp(raw)
    t = _BRACKET_NOISE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    for part in re.split(r"[\s,|/]+", t):
        p = part.strip()
        if len(p) >= 1 and re.match(r"^[\w.@+-]+$", p, flags=re.ASCII):
            return p
        if p.isdigit() and len(p) >= 4:
            return p
    return t.split()[0] if t.split() else ""


def _header_suggests_user_id_column(key: str) -> bool:
    if key == "_seq" or key.startswith("col_"):
        return False
    if "아이디" in key or "회원아이디" in key or "회원" == key:
        return True
    compact = key.replace("_", "")
    if re.search(r"(?i)id", compact) and ("이력" in key or "보기" in key):
        return True
    if compact.lower() in ("id", "userid", "user_id", "memberid"):
        return True
    return False


def extract_applicant_user_id(row: dict[str, str]) -> str:
    raw = pick(row, _APPLICANT_USER_ID_KEYS)
    uid = _clean_user_id_cell(raw) if raw else ""
    if uid:
        return uid
    for k, v in row.items():
        if not v or not str(v).strip():
            continue
        if _header_suggests_user_id_column(k):
            uid = _clean_user_id_cell(str(v))
            if uid:
                return uid
    return ""


def _rpc_safe_timestamp(val: str | None) -> str | None:
    """RPC 가 `::timestamp` 로 파싱 가능한 형태만 통과 (실패 시 전체 배치 오류 방지)."""
    if not val:
        return None
    s = strip_nbsp(str(val))
    if not s:
        return None
    m = re.match(
        r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?",
        s,
    )
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
    if m.group(4) is not None:
        hh, mm = m.group(4).zfill(2), m.group(5).zfill(2)
        ss = (m.group(6) or "00").zfill(2)
        return f"{y}-{mo}-{d} {hh}:{mm}:{ss}"
    return f"{y}-{mo}-{d}"


def row_to_applicant_payload(row: dict[str, str]) -> dict[str, Any] | None:
    user_id = extract_applicant_user_id(row)
    if not user_id:
        return None

    no_raw = pick(row, ["번호", "No", "no"])
    applicant_no: int | None = None
    if no_raw:
        try:
            n = int(str(no_raw).strip())
            applicant_no = n
        except ValueError:
            applicant_no = None

    out: dict[str, Any] = {
        "user_id": user_id,
        "name": pick(row, ["성명", "이름", "name"]) or None,
        "phone": pick(row, ["연락처", "휴대폰", "phone"]) or None,
        "branch": pick(row, ["신청지사", "지점", "branch"]) or None,
        "type": pick(row, ["구분", "유형", "type"]) or None,
        "apply_status": pick(row, ["접수상태", "신청상태", "apply_status"]) or None,
        "exam_status": pick(row, ["시험상태", "exam_status"]) or None,
        "payment_status": pick(row, ["결제", "결제상태", "입금상태", "payment_status"])
        or None,
        "applicant_no": applicant_no,
    }
    ca = _rpc_safe_timestamp(pick(row, ["등록일자", "신청일", "등록일", "created_at"]))
    if ca is not None:
        out["created_at"] = ca
    ua = _rpc_safe_timestamp(pick(row, ["수정일", "updated_at"]))
    if ua is not None:
        out["updated_at"] = ua
    return out


def _page_has_num_one_rows(rows: list[dict[str, str]]) -> bool:
    for row in rows:
        v = row.get("번호")
        if v is not None and str(v).strip() == "1":
            return True
    return False


def ensure_applicant_progress(client: Client) -> None:
    r = (
        client.table("edu_applicant_crawl_progress")
        .select("id")
        .eq("id", APPLICANT_PROGRESS_ID)
        .limit(1)
        .execute()
    )
    if r.data and len(r.data) > 0:
        return
    try:
        client.table("edu_applicant_crawl_progress").insert(
            {
                "id": APPLICANT_PROGRESS_ID,
                "target_edu_seq": None,
                "next_page": 1,
            }
        ).execute()
    except Exception as e:
        log.warning("[신청자] edu_applicant_crawl_progress 시드 실패(이미 있을 수 있음): %s", e)


def get_applicant_progress(client: Client) -> tuple[int | None, int]:
    r = (
        client.table("edu_applicant_crawl_progress")
        .select("target_edu_seq,next_page")
        .eq("id", APPLICANT_PROGRESS_ID)
        .limit(1)
        .execute()
    )
    rows = r.data or []
    if not rows:
        return (None, 1)
    row = rows[0]
    ts = row.get("target_edu_seq")
    edu_seq = int(ts) if ts is not None else None
    np = row.get("next_page", 1)
    try:
        page = int(np) if np is not None else 1
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1
    return (edu_seq, page)


def patch_applicant_progress(
    client: Client,
    target_edu_seq: int | None,
    next_page: int,
) -> None:
    client.table("edu_applicant_crawl_progress").update(
        {
            "target_edu_seq": target_edu_seq,
            "next_page": max(1, next_page),
        }
    ).eq("id", APPLICANT_PROGRESS_ID).execute()


def fetch_min_edu_seq(client: Client) -> int | None:
    r = (
        client.table("legacy_edu")
        .select("seq")
        .order("seq")
        .limit(1)
        .execute()
    )
    rows = r.data or []
    if not rows:
        return None
    s = rows[0].get("seq")
    return int(s) if s is not None else None


def legacy_edu_exists_for_seq(client: Client, edu_seq: int) -> bool:
    r = (
        client.table("legacy_edu")
        .select("id")
        .eq("seq", edu_seq)
        .limit(1)
        .execute()
    )
    return bool(r.data)


def fetch_next_edu_seq_after(client: Client, after_seq: int) -> int | None:
    r = (
        client.table("legacy_edu")
        .select("seq")
        .gt("seq", after_seq)
        .order("seq")
        .limit(1)
        .execute()
    )
    rows = r.data or []
    if not rows:
        return None
    s = rows[0].get("seq")
    return int(s) if s is not None else None


def _display_no_sort_key(row: dict[str, Any]) -> tuple[int, int]:
    """교육 목록 표의 번호(display_no) 오름차순: 1 → 끝번호. 동률·파싱 실패는 seq."""
    dn = row.get("display_no")
    try:
        no = int(str(dn).strip())
    except (ValueError, TypeError, AttributeError):
        no = -1
    seq_raw = row.get("seq")
    try:
        seq = int(seq_raw) if seq_raw is not None else 0
    except (TypeError, ValueError):
        seq = 0
    # 숫자 display_no: 작은 번호 먼저. 비숫자는 맨 뒤(10**9)
    primary = no if no >= 0 else 10**9
    return (primary, seq)


def fetch_all_legacy_edu_seq_display_no(client: Client) -> list[dict[str, Any]]:
    """legacy_edu 전부 (페이지네이션)."""
    out: list[dict[str, Any]] = []
    page_size = 1000
    start = 0
    while True:
        r = (
            client.table("legacy_edu")
            .select("seq,display_no")
            .order("seq")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = r.data or []
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return out


def crawl_applicant_pages_for_edu_seq(
    session: Any,
    settings: Any,
    client: Client,
    edu_seq: int,
    *,
    apply_template: str,
    applicant_table_sel: str,
    max_pages: int,
    page_delay_seconds: float = 0.0,
    start_page: int = 1,
) -> int:
    """한 교육 seq 에 대해 신청 목록 URL 을 페이지 끝까지(빈 표·번호=1·상한). 반환: RPC 영향 합."""
    apply_list_paginated = "{page}" in apply_template
    max_pages = max(1, max_pages)
    total_touched = 0
    page = max(1, start_page)

    while page <= max_pages:
        path = apply_list_fetch_path(apply_template, edu_seq, page)
        url = resolve_url(settings.base_url, path)
        log.info("[신청자] seq=%s 신청목록 p.%s GET %s", edu_seq, page, url)
        html = fetch_list_html_at_path(session, settings, path)
        _h, pairs, _off = parse_edu_table_with_trs(html, applicant_table_sel)
        rows = [r for _, r in pairs]

        if not rows:
            log.info(
                "[신청자] seq=%s 페이지 %s 빈 표 — 이 교육 신청 처리 종료",
                edu_seq,
                page,
            )
            break

        payloads: list[dict[str, Any]] = []
        for row in rows:
            p = row_to_applicant_payload(row)
            if p:
                payloads.append(p)

        if rows and not payloads:
            sample = rows[0]
            log.warning(
                "[신청자] seq=%s 표 %d행인데 user_id 0건. 키=%s",
                edu_seq,
                len(rows),
                list(sample.keys()),
            )

        if payloads:
            n = upsert_edu_applicant_batch(client, edu_seq, payloads)
            total_touched += n
            log.info(
                "[신청자] seq=%s 페이지 %s RPC 영향 행 수=%s",
                edu_seq,
                page,
                n,
            )

        last_flag = _page_has_num_one_rows(rows)
        hit_cap = page >= max_pages
        if not apply_list_paginated or last_flag or hit_cap:
            if apply_list_paginated and last_flag:
                log.info("[신청자] seq=%s 번호=1 도달", edu_seq)
            break

        page += 1
        if page_delay_seconds > 0 and page <= max_pages:
            log.info(
                "[신청자] seq=%s 다음 신청 페이지까지 %.1f초 대기",
                edu_seq,
                page_delay_seconds,
            )
            time.sleep(page_delay_seconds)

    return total_touched


def run_applicants_from_saved_legacy_edu(
    session: Any,
    settings: Any,
    client: Client,
    *,
    apply_template: str,
    applicant_table_sel: str,
    max_pages: int,
    page_delay_seconds: float = 0.0,
    edu_delay_seconds: float = 0.0,
) -> int:
    """
    교육 목록이 이미 legacy_edu 에 있다고 가정.
    display_no(숫자) 내림차순 → 동률은 seq. 각 seq 마다 신청 목록 전부 upsert.
    """
    edu_rows = fetch_all_legacy_edu_seq_display_no(client)
    if not edu_rows:
        log.info("[신청자] legacy_edu 비어 있음 — 건너뜀")
        return 0

    edu_rows_sorted = sorted(edu_rows, key=_display_no_sort_key)
    n = len(edu_rows_sorted)
    log.info(
        "[신청자] DB 기준 전체 순회 시작 (%d건, display_no↑ 1→끝·seq 보조). "
        "진행 테이블 미사용.",
        n,
    )
    grand = 0
    for i, er in enumerate(edu_rows_sorted):
        seq_raw = er.get("seq")
        if seq_raw is None:
            continue
        try:
            edu_seq = int(seq_raw)
        except (TypeError, ValueError):
            continue
        dno = er.get("display_no")
        log.info(
            "[신청자] (%d/%d) display_no=%s seq=%s",
            i + 1,
            n,
            dno,
            edu_seq,
        )
        if not legacy_edu_exists_for_seq(client, edu_seq):
            log.warning("[신청자] seq=%s legacy_edu 없음 — 스킵", edu_seq)
            continue
        grand += crawl_applicant_pages_for_edu_seq(
            session,
            settings,
            client,
            edu_seq,
            apply_template=apply_template,
            applicant_table_sel=applicant_table_sel,
            max_pages=max_pages,
            page_delay_seconds=page_delay_seconds,
            start_page=1,
        )
        if edu_delay_seconds > 0 and i + 1 < n:
            log.info(
                "[신청자] 다음 교육까지 %.1f초 대기 (EDU_APPLICANT_EDU_DELAY_SECONDS)",
                edu_delay_seconds,
            )
            time.sleep(edu_delay_seconds)

    log.info("[신청자] DB 기준 전체 순회 끝 — 누적 RPC 영향 합≈%s", grand)
    return grand


def apply_list_fetch_path(template: str, edu_seq: int, page: int) -> str:
    filled = (
        template.replace("{el_seq}", str(edu_seq)).replace("{seq}", str(edu_seq))
    )
    if "{page}" in filled:
        filled = filled.replace("{page}", str(max(1, page)))
    return filled.strip()


def run_applicants_phase(
    session: Any,
    settings: Any,
    client: Client,
    *,
    apply_template: str,
    applicant_table_sel: str,
    max_pages: int,
    pages_per_run: int,
    page_delay_seconds: float = 0.0,
) -> int:
    """
    Edge applicants 모드 한 번 호출과 동일: pages_per_run 만큼 신청 목록 페이지 처리.
    반환: RPC 영향 행 수 합(대략). 진행 테이블만 바뀌고 0일 수 있음.
    """
    ensure_applicant_progress(client)
    pages_per_run = max(1, min(50, pages_per_run))
    max_pages = max(1, max_pages)
    apply_list_paginated = "{page}" in apply_template

    edu_seq, ap_start_page = get_applicant_progress(client)
    if edu_seq is None:
        edu_seq = fetch_min_edu_seq(client)
        if edu_seq is None:
            log.info("[신청자] legacy_edu 가 비어 있어 신청자 크롤을 건너뜁니다.")
            return 0
        patch_applicant_progress(client, edu_seq, 1)
        ap_start_page = 1

    ap_start_page = max(1, min(ap_start_page, max_pages))

    log.info(
        "[신청자] 단계 시작 (Edge mode=applicants 동일) target_edu_seq=%s start_page=%s pages_per_run=%s",
        edu_seq,
        ap_start_page,
        pages_per_run,
    )

    if not legacy_edu_exists_for_seq(client, edu_seq):
        log.error(
            "[신청자] legacy_edu 에 seq=%s 행이 없습니다. "
            "upsert_edu_applicant_batch 는 INNER JOIN 이라 삽입 0건입니다. "
            "edu 목록 크롤·RPC 후 seq 가 맞는지 확인하세요.",
            edu_seq,
        )
        return 0

    total_touched = 0
    last_page: int | None = None

    for i in range(pages_per_run):
        page = ap_start_page + i
        if page > max_pages:
            log.info("[신청자] page > EDU_MAX_LIST_PAGES, 중단")
            break

        path = apply_list_fetch_path(apply_template, edu_seq, page)
        url = resolve_url(settings.base_url, path)
        log.info("[신청자] 교육 seq=%s 페이지 %s GET %s", edu_seq, page, url)
        html = fetch_list_html_at_path(session, settings, path)
        _h, pairs, _off = parse_edu_table_with_trs(html, applicant_table_sel)
        rows = [r for _, r in pairs]

        if not rows:
            next_seq = fetch_next_edu_seq_after(client, edu_seq)
            wrap = next_seq if next_seq is not None else fetch_min_edu_seq(client)
            log.info(
                "[신청자] 빈 목록 (교육 seq=%s). 다음 target_edu_seq=%s, 1페이지.",
                edu_seq,
                wrap,
            )
            patch_applicant_progress(client, wrap, 1)
            last_page = page
            break

        payloads: list[dict[str, Any]] = []
        for row in rows:
            p = row_to_applicant_payload(row)
            if p:
                payloads.append(p)

        if rows and not payloads:
            sample = rows[0]
            log.warning(
                "[신청자] 표는 %d행인데 user_id 추출에 성공한 행이 0건입니다. "
                "첫 행 컬럼 키=%s 샘플=%s",
                len(rows),
                list(sample.keys()),
                {
                    k: ((str(v)[:80] + "…") if len(str(v)) > 80 else str(v))
                    for k, v in sample.items()
                    if k != "_seq"
                },
            )

        if payloads:
            n = upsert_edu_applicant_batch(client, edu_seq, payloads)
            total_touched += n
            log.info(
                "[신청자] 교육 seq=%s 페이지 %s RPC 영향 행 수=%s",
                edu_seq,
                page,
                n,
            )

        last_page = page
        last_flag = _page_has_num_one_rows(rows)
        hit_cap = page >= max_pages
        done_with_edu = not apply_list_paginated or last_flag or hit_cap

        if done_with_edu:
            next_seq = fetch_next_edu_seq_after(client, edu_seq)
            wrap = next_seq if next_seq is not None else fetch_min_edu_seq(client)
            log.info(
                "[신청자] 교육 seq=%s 구간 완료 → 다음 seq=%s, 1페이지.",
                edu_seq,
                wrap,
            )
            patch_applicant_progress(client, wrap, 1)
            break

        patch_applicant_progress(client, edu_seq, page + 1)

        if (
            page_delay_seconds > 0
            and i + 1 < pages_per_run
            and page + 1 <= max_pages
        ):
            log.info(
                "[신청자] 다음 신청 목록 페이지까지 %.1f초 대기…",
                page_delay_seconds,
            )
            time.sleep(page_delay_seconds)

    log.info(
        "[신청자] 단계 종료 (마지막 처리 페이지=%s, 누적 RPC 영향 합≈%s)",
        last_page,
        total_touched,
    )
    return total_touched
