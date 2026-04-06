"""
Fetch a page after admin login and save HTML for inspection (no Supabase).

Usage (from repo root, with .env loaded):

  python -m crawler.probe

Env:
  BASE_URL, LOGIN_PATH, ADMIN_USER, ADMIN_PASSWORD
  LOGIN_USER_FIELD (default m_id), LOGIN_PASS_FIELD (default m_pass)
  PROBE_TARGET_URL — full URL to fetch after login (default: ex-tech member list p1)
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

from crawler.session import build_session, login, resolve_url
from crawler.config import Settings, load_settings

load_dotenv()

log = logging.getLogger(__name__)

DEFAULT_MEMBER_LIST = (
    "http://www.ex-techkorea.com/admin/member/member_list.html"
    "?select_key=&input_key=&search=&sort=member&type=P&page=1"
)


def probe_with_full_settings(settings: Settings, target_url: str, out_dir: Path) -> None:
    session = build_session(settings)
    login(session, settings)
    log.info("GET probe target: %s", target_url)
    r = session.get(target_url, timeout=120)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding or "utf-8"
    text = r.text
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "probe_last.html").write_text(text, encoding="utf-8")
    log.info("Saved %s (%d bytes)", out_dir / "probe_last.html", len(text))

    for m in set(re.findall(r"[a-zA-Z0-9_/]+\.php(?:\?[^\"'>\s]*)?", text)):
        if "_ok" in m or "member" in m.lower() or "list" in m.lower():
            print("php ref:", m)

    scripts = re.findall(r'src=["\']([^"\']+)["\']', text, re.I)
    for s in scripts[:30]:
        if "function" in s or "admin" in s:
            print("script:", s)

    print("\n--- body preview (first 2500 chars) ---\n")
    print(text[:2500])


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    target = (os.getenv("PROBE_TARGET_URL") or DEFAULT_MEMBER_LIST).strip()
    out_dir = Path(os.getenv("PROBE_OUT_DIR") or "_debug")

    # If LIST_OK_PATH missing, run.py-style settings will fail — probe-only env path.
    if not os.getenv("LIST_OK_PATH"):
        base = os.getenv("BASE_URL", "").strip().rstrip("/")
        login_path = os.getenv("LOGIN_PATH", "").strip()
        user = os.getenv("ADMIN_USER", "").strip()
        pw = os.getenv("ADMIN_PASSWORD", "").strip()
        if not all([base, login_path, user, pw]):
            log.error(
                "Set BASE_URL, LOGIN_PATH, ADMIN_USER, ADMIN_PASSWORD "
                "(and optionally LIST_OK_PATH for crawler.run)."
            )
            return 1
        lu = os.getenv("LOGIN_USER_FIELD", "m_id") or "m_id"
        lp = os.getenv("LOGIN_PASS_FIELD", "m_pass") or "m_pass"
        import json

        settings = Settings(
            base_url=base,
            login_path=login_path,
            list_ok_path="/admin/member/member_list_ok.php",
            login_user_field=lu,
            login_pass_field=lp,
            admin_user=user,
            admin_password=pw,
            login_extra_fields=json.loads(os.getenv("LOGIN_EXTRA_FIELDS_JSON") or "{}"),
            list_method="GET",
            list_post_body=None,
            supabase_url=os.getenv("SUPABASE_URL") or "https://placeholder.supabase.co",
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "x",
            supabase_table=os.getenv("SUPABASE_TABLE") or "crawl_rows",
            row_id_header=os.getenv("ROW_ID_HEADER"),
            table_selector=os.getenv("TABLE_SELECTOR") or "table",
            verify_tls=(os.getenv("VERIFY_TLS", "true") or "true").lower()
            in ("1", "true", "yes"),
            skip_supabase=True,
            fetch_member_memo=False,
            member_form_path="/admin/member/member_form.html",
            member_form_extra_query="",
            memo_request_delay_ms=0,
            save_to_members_crawled=False,
            max_list_pages=2000,
        )
    else:
        settings = load_settings()

    login_url = resolve_url(settings.base_url, settings.login_path)
    print("Login URL:", login_url)
    print("Probe target:", target)
    print("Output dir:", out_dir.resolve())
    print()

    probe_with_full_settings(settings, target, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
