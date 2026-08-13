from orders import list_orders


def test_first_page_starts_from_first_order():
    assert list_orders(1, 10) == 0
