from __future__ import annotations

from typing import Any


def parse_login_id_social(raw: str) -> tuple[str, str | None]:
    s = (raw or "").strip()
    if not s:
        return "", None
    social: str | None = None
    if "네이버" in s:
        social = "naver"
    elif "카카오" in s:
        social = "kakao"
    if " (" in s:
        login_id = s.split(" (", 1)[0].strip()
    elif " [" in s:
        login_id = s.split(" [", 1)[0].strip()
    else:
        parts = s.split()
        login_id = parts[0] if parts else ""
    return login_id, social


def page_has_list_num_one(rows: list[dict[str, Any]]) -> bool:
    for r in rows:
        v = r.get("번호")
        if v is None:
            continue
        if str(v).strip() == "1":
            return True
    return False


def crawl_row_to_member_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    seq_raw = row.get("_seq")
    if seq_raw is None or str(seq_raw).strip() == "":
        return None
    try:
        seq = int(str(seq_raw).strip())
    except ValueError:
        return None

    num_val: int | None = None
    nr = row.get("번호")
    if nr is not None and str(nr).strip() != "":
        try:
            num_val = int(str(nr).strip())
        except ValueError:
            num_val = None

    login_raw = row.get("아이디") or ""
    login_id, social = parse_login_id_social(str(login_raw))

    sub = row.get("sub") if isinstance(row.get("sub"), dict) else {}
    memo = ""
    if isinstance(sub, dict):
        memo = str(sub.get("메모") or "").strip()

    jd = row.get("가입일")
    join_date = str(jd).strip() if jd is not None else ""

    return {
        "seq": seq,
        "num": num_val,
        "login_id": login_id or None,
        "social_type": social,
        "name": (str(row.get("이름") or "").strip() or None),
        "phone": (str(row.get("연락처") or "").strip() or None),
        "email": (str(row.get("이메일") or "").strip() or None),
        "join_date": join_date or None,
        "status": (str(row.get("상태") or "").strip() or None),
        "memo": memo or None,
    }
