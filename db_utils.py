"""
Shared database utilities for Wildlife Watcher Supabase interaction.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client

def fetch_all_rows(client: "Client", table: str, select: str, order_by: str = "created_at", include_deleted: bool = False):
    """Fetch all rows from a table, handling Supabase's 1000-row default limit."""
    all_rows = []
    offset = 0
    page_size = 1000

    while True:
        query = client.table(table).select(select)
        if not include_deleted:
            query = query.is_("deleted_at", "null")

        response = (
            query.order(order_by, desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )

        if not response.data:
            break

        all_rows.extend(response.data)

        if len(response.data) < page_size:
            break  # Last page

        offset += page_size

    return all_rows
