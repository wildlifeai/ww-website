# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Paginated Supabase query helper.

Direct port of db_utils.py fetch_all_rows() for backend use.
"""

from typing import Any, Dict, List

import structlog

logger = structlog.get_logger()


def fetch_all_rows(
    client, table: str, select: str = "*", page_size: int = 1000
) -> List[Dict[str, Any]]:
    """Fetch all rows from a Supabase table using pagination.

    Args:
        client: Supabase client instance.
        table: Table name.
        select: Column selection string.
        page_size: Rows per page (max 1000 for Supabase).

    Returns:
        List of all rows as dicts.
    """
    all_rows: List[Dict[str, Any]] = []
    offset = 0

    while True:
        response = (
            client.table(table)
            .select(select)
            .range(offset, offset + page_size - 1)
            .execute()
        )

        if not response.data:
            break

        all_rows.extend(response.data)

        if len(response.data) < page_size:
            break

        offset += page_size

    logger.debug("fetch_all_rows", table=table, total=len(all_rows))
    return all_rows
