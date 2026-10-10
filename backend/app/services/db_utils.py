# Copyright (c) 2024
# SPDX-License-Identifier: GPL-3.0-or-later
"""Supabase query helpers: typed row access and pagination.

fetch_all_rows() is a direct port of db_utils.py fetch_all_rows() for backend use.
"""

from typing import Any, Dict, List, Optional, cast

import structlog
from postgrest import APIResponse

logger = structlog.get_logger()

Row = Dict[str, Any]


def rows_of(response: APIResponse) -> List[Row]:
    """The rows of a query response, or [] when it has none.

    postgrest types ``data`` as a list of any JSON value, so reading a column off a row does not
    type-check. A select, insert, update or table-returning RPC yields JSON objects, so each row
    is a dict. Use it instead of ``response.data or []``.
    """
    return cast(List[Row], response.data or [])


def row_of(response: Optional[APIResponse]) -> Optional[Row]:
    """The row of a ``single()`` or ``maybe_single()`` response, or None when there is none.

    ``maybe_single().execute()`` returns None, not an empty response, when no row matches.
    """
    if response is None:
        return None
    return cast(Optional[Row], response.data) or None


def fetch_all_rows(client, table: str, select: str = "*", page_size: int = 1000) -> List[Dict[str, Any]]:
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
        response = client.table(table).select(select).range(offset, offset + page_size - 1).execute()

        if not response.data:
            break

        all_rows.extend(response.data)

        if len(response.data) < page_size:
            break

        offset += page_size

    logger.debug("fetch_all_rows", table=table, total=len(all_rows))
    return all_rows
