def create_order(customer_id: str, note: str | None = None) -> dict:
    normalized_note = note.strip()
    return {"customer_id": customer_id, "note": normalized_note}
