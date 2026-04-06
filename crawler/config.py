from __future__ import annotations

import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name, default)
    if v is not None and isinstance(v, str) and v.strip() == "":
        return default
    return v


def _require(name: str) -> str:
    v = _get(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


@dataclass(frozen=True)
class Settings:
    base_url: str
    login_path: str
    list_ok_path: str
    login_user_field: str
    login_pass_field: str
    admin_user: str
    admin_password: str
    login_extra_fields: dict
    list_method: str
    list_post_body: dict | None
    supabase_url: str
    supabase_service_role_key: str
    supabase_table: str
    row_id_header: str | None
    table_selector: str
    verify_tls: bool
    skip_supabase: bool
    fetch_member_memo: bool
    member_form_path: str
    member_form_extra_query: str
    memo_request_delay_ms: int
    save_to_members_crawled: bool
    max_list_pages: int


def load_settings() -> Settings:
    extra_raw = _get("LOGIN_EXTRA_FIELDS_JSON", "{}") or "{}"
    try:
        login_extra = json.loads(extra_raw)
        if not isinstance(login_extra, dict):
            raise ValueError("LOGIN_EXTRA_FIELDS_JSON must be a JSON object")
    except json.JSONDecodeError as e:
        raise RuntimeError("LOGIN_EXTRA_FIELDS_JSON is not valid JSON") from e

    list_method = (_get("LIST_HTTP_METHOD", "GET") or "GET").upper()
    list_post_raw = _get("LIST_POST_BODY_JSON")
    list_post_body: dict | None = None
    if list_post_raw:
        try:
            parsed = json.loads(list_post_raw)
            if not isinstance(parsed, dict):
                raise ValueError("LIST_POST_BODY_JSON must be a JSON object")
            list_post_body = parsed
        except json.JSONDecodeError as e:
            raise RuntimeError("LIST_POST_BODY_JSON is not valid JSON") from e

    verify_tls = (_get("VERIFY_TLS", "true") or "true").lower() in (
        "1",
        "true",
        "yes",
    )

    skip_supabase = (_get("SKIP_SUPABASE", "false") or "false").lower() in (
        "1",
        "true",
        "yes",
    )

    if skip_supabase:
        supabase_url = _get("SUPABASE_URL") or "https://skipped.local"
        supabase_key = _get("SUPABASE_SERVICE_ROLE_KEY") or "skipped"
    else:
        supabase_url = _require("SUPABASE_URL")
        supabase_key = _require("SUPABASE_SERVICE_ROLE_KEY")

    fetch_member_memo = (_get("FETCH_MEMBER_MEMO", "false") or "false").lower() in (
        "1",
        "true",
        "yes",
    )
    memo_delay_raw = _get("MEMO_REQUEST_DELAY_MS", "0") or "0"
    try:
        memo_request_delay_ms = max(0, int(memo_delay_raw))
    except ValueError as e:
        raise RuntimeError("MEMO_REQUEST_DELAY_MS must be an integer") from e

    save_to_members_crawled = (
        _get("SAVE_TO_MEMBERS_CRAWLED", "false") or "false"
    ).lower() in ("1", "true", "yes")
    max_pages_raw = _get("MAX_LIST_PAGES", "2000") or "2000"
    try:
        max_list_pages = max(1, int(max_pages_raw))
    except ValueError as e:
        raise RuntimeError("MAX_LIST_PAGES must be an integer") from e

    return Settings(
        base_url=_require("BASE_URL").rstrip("/"),
        login_path=_require("LOGIN_PATH"),
        list_ok_path=_require("LIST_OK_PATH"),
        login_user_field=_get("LOGIN_USER_FIELD", "user_id") or "user_id",
        login_pass_field=_get("LOGIN_PASS_FIELD", "user_pw") or "user_pw",
        admin_user=_require("ADMIN_USER"),
        admin_password=_require("ADMIN_PASSWORD"),
        login_extra_fields=login_extra,
        list_method=list_method,
        list_post_body=list_post_body,
        supabase_url=supabase_url,
        supabase_service_role_key=supabase_key,
        supabase_table=_get("SUPABASE_TABLE", "crawl_rows") or "crawl_rows",
        row_id_header=_get("ROW_ID_HEADER"),
        table_selector=_get("TABLE_SELECTOR", "table") or "table",
        verify_tls=verify_tls,
        skip_supabase=skip_supabase,
        fetch_member_memo=fetch_member_memo,
        member_form_path=_get("MEMBER_FORM_PATH", "/admin/member/member_form.html")
        or "/admin/member/member_form.html",
        member_form_extra_query=_get(
            "MEMBER_FORM_EXTRA_QUERY",
            "select_key=&input_key=&search=&sort=member&type=P&page=1",
        )
        or "",
        memo_request_delay_ms=memo_request_delay_ms,
        save_to_members_crawled=save_to_members_crawled,
        max_list_pages=max_list_pages,
    )
