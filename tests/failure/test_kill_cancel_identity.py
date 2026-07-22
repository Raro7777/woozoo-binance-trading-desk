from paper_engine.persistence import kill_cancel_id


def test_kill_cancel_id_binds_full_activation_and_order_ids() -> None:
    shared_prefix = "a" * 16
    first_order = shared_prefix + "1" * 48
    second_order = shared_prefix + "2" * 48
    activation = "b" * 64

    first = kill_cancel_id(activation, first_order)
    replay = kill_cancel_id(activation, first_order)
    second = kill_cancel_id(activation, second_order)
    other_activation = kill_cancel_id("c" * 64, first_order)

    assert len(first) == 64
    assert first == replay
    assert first != second
    assert first != other_activation
