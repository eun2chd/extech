from __future__ import annotations

from urllib.parse import parse_qsl, urlencode


def list_path_for_page(list_ok_path: str, page: int) -> str:
    """
    list_ok_path: relative path + query, e.g. /admin/member/member_list.html?...&page=1
    Ensures query param page=<n>.
    """
    page = max(1, int(page))
    if "?" in list_ok_path:
        path, q = list_ok_path.split("?", 1)
        pairs = parse_qsl(q, keep_blank_values=True)
    else:
        path, pairs = list_ok_path, []
    d = dict(pairs)
    d["page"] = str(page)
    new_q = urlencode(list(d.items()))
    return f"{path}?{new_q}"
