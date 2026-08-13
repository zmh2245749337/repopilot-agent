from orders import create_order


def test_missing_note_is_safe():
    assert create_order("customer-1") == {"customer_id": "customer-1", "note": ""}


def test_present_note_is_trimmed():
    assert create_order("customer-1", "  urgent ") ["note"] == "urgent"
