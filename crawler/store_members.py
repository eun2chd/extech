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
        touched = 0
        if isinstance(n, int):
            touched = n
            total += touched
        elif n is None:
            pass
        else:
            try:
                touched = int(n)
                total += touched
            except (TypeError, ValueError):
                log.warning("unexpected RPC return: %r", n)
        if len(chunk) > 0:
            log.info(
                "[members_crawled] RPC 영향 행 수≈%d (이 배치 %d건, insert+update 합계)",
                touched,
                len(chunk),
            )
    log.info("[members_crawled] 이번 호출 합계: RPC 영향 행 수≈%d", total)
    return total
