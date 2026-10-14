import pytest

from app.seats import (
    SeatError,
    are_adjacent,
    contiguous_blocks,
    find_block,
    hold_expires_at,
    hold_is_live,
    is_block_available,
    parse_seat,
    quote,
    row_tier,
    seat_price,
    seconds_left,
)


def test_parse_seat_accepts_lowercase():
    assert parse_seat("a1") == ("A", 1)


@pytest.mark.parametrize("label", ["1A", "A0", "AA1", "Q25", ""])
def test_parse_seat_rejects_invalid_labels(label):
    with pytest.raises(SeatError):
        parse_seat(label)


def test_aisle_separates_seats_6_and_7():
    assert are_adjacent("A5", "A6")
    assert not are_adjacent("A6", "A7")


def test_different_rows_are_not_adjacent():
    assert not are_adjacent("A5", "B5")


def test_contiguous_blocks_split_at_gaps_and_aisles():
    blocks = contiguous_blocks(["A5", "A6", "A7", "A9", "A10"])
    assert blocks == [["A5", "A6"], ["A7"], ["A9", "A10"]]


def test_find_block_returns_none_when_not_enough_seats():
    assert find_block(["A1", "A2"], 3) is None


def test_find_block_of_one_seat():
    assert find_block(["A1", "A2"], 1) == ["A2"]


def test_is_block_available_requires_all_seats_to_be_free():
    assert is_block_available(["A1", "A2"], ["A1", "A2", "A3"])
    assert not is_block_available(["A1", "A2"], ["A1"])


@pytest.mark.parametrize(
    ("row", "expected"),
    [("C", "front"), ("D", "middle"), ("H", "middle"), ("I", "back")],
)
def test_row_tier_boundaries(row, expected):
    assert row_tier(row) == expected


def test_seat_price_uses_row_tier():
    assert seat_price("A1", 100) == 175.0
    assert seat_price("I1", 100) == 100.0


def test_quote_adds_one_booking_fee_per_order():
    result = quote(["A1", "I1"], 100)
    assert result["subtotal"] == 275.0
    assert result["fee"] == 20.0
    assert result["total"] == 295.0


def test_quote_rejects_empty_order():
    with pytest.raises(SeatError):
        quote([], 100)


def test_hold_expiry_timestamp():
    assert hold_expires_at(1000) == 1600.0


def test_seconds_left_never_goes_negative():
    assert seconds_left(1000, 1700) == 0.0


def test_hold_is_live_before_expiry():
    assert hold_is_live(1000, 1599)


def test_hold_is_dead_exactly_at_expiry():
    assert not hold_is_live(1000, 1600)


def test_hold_is_dead_after_expiry():
    assert not hold_is_live(1000, 1601)

