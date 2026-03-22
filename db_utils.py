"""
Shared database utilities for Wildlife Watcher Supabase interaction.
"""

def fetch_all_rows(client, table: str, select: str, order_by: str = "created_at"):
    """Fetch all rows from a table, handling Supabase's 1000-row default limit."""
    all_rows = []
    offset = 0
    page_size = 1000

    while True:
        response = (
            client.table(table)
            .select(select)
            .is_("deleted_at", "null")
            .order(order_by, desc=True)
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
