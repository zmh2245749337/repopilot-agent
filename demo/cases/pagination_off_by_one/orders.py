def list_orders(page: int, page_size: int) -> int:
    """Return the starting row for one-based page numbers."""
    offset = page * page_size
    return offset
