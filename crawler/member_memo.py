from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import requests
from bs4 import BeautifulSoup

from crawler.session import resolve_url

if TYPE_CHECKING:
    from crawler.config import Settings

log = logging.getLogger(__name__)


def parse_m_memo(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    ta = soup.select_one("textarea#m_memo") or soup.select_one('textarea[name="m_memo"]')
    if ta is None:
        return ""
    text = ta.get_text()
    return (text or "").strip()


def member_form_url(settings: Settings, seq: str) -> str:
    path = settings.member_form_path.strip()
    if "?" in path:
        path = path.split("?", 1)[0]
    q = f"mode=modify&seq={seq}"
    extra = (settings.member_form_extra_query or "").strip()
    if extra:
        q = q + "&" + extra.lstrip("&")
    return resolve_url(settings.base_url, f"{path}?{q}")


def enrich_rows_with_memo(
    session: requests.Session,
    settings: Settings,
    rows: list[dict[str, Any]],
) -> None:
    if not settings.fetch_member_memo:
        return
    delay = max(0, settings.memo_request_delay_ms) / 1000.0
    for idx, row in enumerate(rows):
        seq = row.get("_seq")
        memo = ""
        if seq:
            url = member_form_url(settings, str(seq))
            try:
                if idx > 0 and delay:
                    time.sleep(delay)
                r = session.get(url, timeout=90)
                r.raise_for_status()
                r.encoding = r.apparent_encoding or r.encoding or "utf-8"
                memo = parse_m_memo(r.text)
            except OSError as e:
                log.warning("memo fetch failed seq=%s: %s", seq, e)
        row["sub"] = {"메모": memo}
