from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import requests
from requests.exceptions import (
    ChunkedEncodingError,
    ConnectionError,
    ConnectTimeout,
    ReadTimeout,
    Timeout,
)
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeoutError

if TYPE_CHECKING:
    from crawler.config import Settings

log = logging.getLogger(__name__)

# 목록 GET/POST (connect, read) 초 — `RESUME_LIST_HTTP_TIMEOUT` 로 늘리면 page=97 같은 느린 응답에 유리
_LIST_HTTP_TIMEOUT = float(os.getenv("RESUME_LIST_HTTP_TIMEOUT", "120") or "120")

_LIST_FETCH_RETRYABLE = (
    ConnectionError,
    ChunkedEncodingError,
    Timeout,
    ReadTimeout,
    ConnectTimeout,
    Urllib3ReadTimeoutError,
)


def resolve_url(base: str, path: str) -> str:
    path = (path or "").strip()
    if path.startswith("http://") or path.startswith("https://"):
        return path
    b = (base or "").strip()
    if b and not b.endswith("/"):
        b = b + "/"
    return urljoin(b, path)


def build_session(settings: Settings) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; AdminCrawler/0.1; +https://github.com/)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        }
    )
    s.verify = settings.verify_tls
    return s


def login(session: requests.Session, settings: Settings) -> None:
    url = resolve_url(settings.base_url, settings.login_path)
    payload = {
        **settings.login_extra_fields,
        settings.login_user_field: settings.admin_user,
        settings.login_pass_field: settings.admin_password,
    }
    log.info("POST login: %s", url)
    r = session.post(url, data=payload, timeout=60)
    r.raise_for_status()
    if "PHPSESSID" not in session.cookies.get_dict():
        log.warning("No PHPSESSID cookie after login; site may use a different cookie name.")


def fetch_list_html_at_path(
    session: requests.Session,
    settings: Settings,
    list_path_with_query: str,
) -> str:
    url = resolve_url(settings.base_url, list_path_with_query)
    max_attempts = 6
    backoff_s = 2.0
    attempt = 0
    while True:
        try:
            if settings.list_method == "POST":
                log.info("POST list: %s", url)
                r = session.post(
                    url,
                    data=settings.list_post_body or {},
                    timeout=_LIST_HTTP_TIMEOUT,
                )
            else:
                log.info("GET list: %s", url)
                r = session.get(url, timeout=_LIST_HTTP_TIMEOUT)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or r.encoding or "utf-8"
            return r.text
        except _LIST_FETCH_RETRYABLE as e:
            attempt += 1
            if attempt >= max_attempts:
                raise
            wait = backoff_s * (2 ** (attempt - 1))
            log.warning(
                "list fetch failed (%s/%s), retry in %.1fs: %s",
                attempt,
                max_attempts,
                wait,
                e,
            )
            time.sleep(wait)


def fetch_list_html(session: requests.Session, settings: Settings) -> str:
    return fetch_list_html_at_path(session, settings, settings.list_ok_path)
