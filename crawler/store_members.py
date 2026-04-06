from __future__ import annotations

import logging
from typing import Any

from supabase import Client

log = logging.getLogger(__name__)

BATCH_SIZE = 200


def insert_members_crawled_batch(client: Client, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i : i + BATCH_SIZE]
        res = client.rpc(
            "insert_members_crawled_batch",
            {"p_rows": chunk},
        ).execute()
        n = res.data
        if isinstance(n, int):
            total += n
        elif n is None:
            pass
        else:
            try:
                total += int(n)
            except (TypeError, ValueError):
                log.warning("unexpected RPC return: %r", n)
    log.info("members_crawled: inserted %d new rows (skipped existing seq)", total)
    return total
