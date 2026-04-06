from __future__ import annotations

import logging
from typing import Any

from supabase import Client, create_client

from crawler.config import Settings
from crawler.parse_table import pick_external_id

log = logging.getLogger(__name__)


def make_supabase(settings: Settings) -> Client:
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
    )


def upsert_rows(
    client: Client,
    settings: Settings,
    rows: list[dict[str, Any]],
) -> int:
    if not rows:
        return 0
    table = settings.supabase_table
    batch: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        ext = pick_external_id(row, settings.row_id_header)
        if not ext:
            skipped += 1
            continue
        batch.append({"external_id": ext, "row_data": row})
    if skipped:
        log.warning("Skipped %d rows without external_id", skipped)
    if not batch:
        return 0

    # on_conflict requires unique constraint on external_id
    client.table(table).upsert(batch, on_conflict="external_id").execute()
    log.info("Upserted %d rows into %s", len(batch), table)
    return len(batch)
