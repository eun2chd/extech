from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


def _cell_text(cell) -> str:
    return re.sub(r"\s+", " ", cell.get_text(separator=" ", strip=True) or "").strip()


def _normalize_key(s: str) -> str:
    key = re.sub(r"\s+", "_", s.strip())
    key = re.sub(r"[^\w가-힣]+", "", key, flags=re.UNICODE)
    return key or "col"


def parse_html_table(html: str, table_selector: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one(table_selector)
    if table is None:
        log.warning("No table matched selector %r; trying first table in document.", table_selector)
        table = soup.find("table")
    if table is None:
        raise RuntimeError("Could not find a <table> in the response.")

    rows = table.find_all("tr")
    if not rows:
        return []

    header_cells = rows[0].find_all(["th", "td"])
    headers = [_normalize_key(_cell_text(c)) for c in header_cells]
    # de-dupe keys while keeping order
    seen: dict[str, int] = {}
    unique_headers: list[str] = []
    for h in headers:
        n = seen.get(h, 0)
        seen[h] = n + 1
        unique_headers.append(h if n == 0 else f"{h}_{n + 1}")

    out: list[dict[str, Any]] = []
    for tr in rows[1:]:
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        row: dict[str, Any] = {}
        cb = tr.select_one('input[type="checkbox"][name*="seq_list"]')
        if cb and cb.get("value"):
            row["_seq"] = str(cb["value"]).strip()
        for i, c in enumerate(cells):
            key = unique_headers[i] if i < len(unique_headers) else f"col_{i}"
            row[key] = _cell_text(c)
        if any(v for v in row.values()):
            out.append(row)
    log.info("Parsed %d data rows", len(out))
    return out


def pick_external_id(
    row: dict[str, Any],
    row_id_header: str | None,
) -> str | None:
    seq = row.get("_seq")
    if seq is not None and str(seq).strip():
        return str(seq).strip()
    if row_id_header:
        for k, v in row.items():
            if k == row_id_header or row_id_header in k:
                s = str(v).strip()
                return s or None
        # try loose match on original header substring
        for k, v in row.items():
            if row_id_header.replace("_", "") in k.replace("_", ""):
                s = str(v).strip()
                return s or None
    for key in ("번호", "No", "no", "idx", "id", "신청번호", "일련번호"):
        for k, v in row.items():
            if key in k:
                s = str(v).strip()
                if s:
                    return s
    first = next(iter(row.values()), None)
    if first is not None:
        s = str(first).strip()
        return s or None
    return None
