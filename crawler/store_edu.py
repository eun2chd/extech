from __future__ import annotations

import logging
from typing import Any

from supabase import Client

log = logging.getLogger(__name__)

BATCH_SIZE = 200


def _dedupe_applicant_payloads_by_user_id(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """한 RPC 배치 안에 동일 user_id 가 두 번 있으면 Postgres 21000 오류가 난다. 뒤 행이 이긴다."""
    by_uid: dict[str, dict[str, Any]] = {}
    for r in rows:
        uid = str(r.get("user_id") or "").strip()
        if not uid:
            continue
        by_uid[uid] = r
    return list(by_uid.values())


def upsert_edu_batch(client: Client, rows: list[dict[str, Any]]) -> int:
    """Edge `upsert_edu_batch` 와 동일 RPC. 반환값은 PostgreSQL `get diagnostics row_count` (영향 행 수)."""
    if not rows:
        log.info("[legacy_edu] upsert: 건너뜀 (행 0건)")
        return 0
    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i : i + BATCH_SIZE]
        lo, hi = i + 1, i + len(chunk)
        log.info(
            "[legacy_edu] upsert: RPC 호출 중… (배치 %d~%d번째 행, %d건)",
            lo,
            hi,
            len(chunk),
        )
        res = client.rpc("upsert_edu_batch", {"p_rows": chunk}).execute()
        n = res.data
        touched = 0
        if isinstance(n, int):
            touched = n
        elif n is not None:
            try:
                touched = int(n)
            except (TypeError, ValueError):
                log.warning("[legacy_edu] upsert: RPC 반환값 이상함: %r", n)
        total += touched
        log.info(
            "[legacy_edu] insert 성공! 이 배치 DB 영향 행 수=%d (누적 %d). "
            "참고: upsert 이라 같은 seq 는 건너뛰지 않고 행을 갱신합니다.",
            touched,
            total,
        )
    log.info("[legacy_edu] upsert 완료: 총 영향 행 수 합계=%d", total)
    return total


def upsert_edu_applicant_batch(
    client: Client, edu_seq: int, rows: list[dict[str, Any]]
) -> int:
    """Edge `upsert_edu_applicant_batch` 와 동일 RPC."""
    if not rows:
        log.info("[legacy_edu_applicant] upsert: 건너뜀 (행 0건) seq=%s", edu_seq)
        return 0
    n0 = len(rows)
    rows = _dedupe_applicant_payloads_by_user_id(rows)
    if len(rows) < n0:
        log.info(
            "[legacy_edu_applicant] 동일 user_id 중복 제거: %d → %d건 (edu_seq=%s, "
            "한 INSERT·ON CONFLICT 에서 같은 키 두 번이면 DB 오류 21000)",
            n0,
            len(rows),
            edu_seq,
        )
    if not rows:
        log.info("[legacy_edu_applicant] upsert: user_id 유효 행 0건 seq=%s", edu_seq)
        return 0
    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i : i + BATCH_SIZE]
        lo, hi = i + 1, i + len(chunk)
        log.info(
            "[legacy_edu_applicant] upsert: RPC (edu_seq=%s) 배치 %d~%d (%d건)",
            edu_seq,
            lo,
            hi,
            len(chunk),
        )
        try:
            res = client.rpc(
                "upsert_edu_applicant_batch",
                {"p_edu_seq": edu_seq, "p_rows": chunk},
            ).execute()
        except Exception:
            log.exception(
                "[legacy_edu_applicant] RPC upsert_edu_applicant_batch 실패 "
                "(edu_seq=%s, 배치 %d~%d행)",
                edu_seq,
                lo,
                hi,
            )
            raise
        n = res.data
        touched = 0
        if isinstance(n, int):
            touched = n
        elif n is not None:
            try:
                touched = int(n)
            except (TypeError, ValueError):
                log.warning("[legacy_edu_applicant] RPC 반환 이상: %r", n)
        total += touched
        log.info(
            "[legacy_edu_applicant] 이 배치 영향 행 수=%d (누적 %d)",
            touched,
            total,
        )
    log.info(
        "[legacy_edu_applicant] 완료 edu_seq=%s 총 영향 합=%d",
        edu_seq,
        total,
    )
    return total
