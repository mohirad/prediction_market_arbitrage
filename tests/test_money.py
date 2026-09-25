from fractions import Fraction

import pytest

from arb.money import (
    cents_to_pbp,
    decimal_str_to_pbp,
    json_number_to_str,
    kalshi_fee_cents,
    notional_cents,
    notional_cents_ceil,
    parse_dollars_to_cents,
    pbp_to_cents_exact,
    spread_pbp,
)


def test_cents_to_pbp_roundtrip():
    for c in [0, 1, 50, 99, 100]:
        assert pbp_to_cents_exact(cents_to_pbp(c)) == c


def test_cents_to_pbp_rejects_out_of_range():
    with pytest.raises(ValueError):
        cents_to_pbp(101)
    with pytest.raises(ValueError):
        cents_to_pbp(-1)


def test_pbp_to_cents_exact_rejects_non_cent_aligned():
    with pytest.raises(ValueError):
        pbp_to_cents_exact(1234)  # not a multiple of 100


def test_decimal_str_to_pbp_basic():
    assert decimal_str_to_pbp("0.63") == 6300
    assert decimal_str_to_pbp("0.001") == 10
    assert decimal_str_to_pbp("1") == 10000
    assert decimal_str_to_pbp("0") == 0


def test_decimal_str_to_pbp_rejects_sub_pbp_precision():
    with pytest.raises(ValueError):
        decimal_str_to_pbp("0.00001")


def test_decimal_str_to_pbp_rejects_out_of_range():
    with pytest.raises(ValueError):
        decimal_str_to_pbp("1.5")
    with pytest.raises(ValueError):
        decimal_str_to_pbp("-0.1")


def test_notional_cents_floor_vs_ceil():
    # 3 contracts @ 33.33c (3333 pbp) = 99.99c
    price = 3333
    assert notional_cents(3, price) == 99
    assert notional_cents_ceil(3, price) == 100


def test_notional_cents_exact_when_divisible():
    price = cents_to_pbp(50)
    assert notional_cents(4, price) == 200
    assert notional_cents_ceil(4, price) == 200


def test_kalshi_fee_cents_known_value():
    # 100 contracts @ 50c, multiplier 0.07:
    # fee = ceil(0.07 * 100 * 0.5 * 0.5 * 100) = ceil(175) = 175 cents
    price = cents_to_pbp(50)
    fee = kalshi_fee_cents(100, price, Fraction(7, 100))
    assert fee == 175


def test_kalshi_fee_cents_rounds_up():
    # 1 contract @ 1c: fee = ceil(0.07 * 1 * 0.01 * 0.99 * 100) = ceil(0.0693) = 1
    price = cents_to_pbp(1)
    fee = kalshi_fee_cents(1, price, Fraction(7, 100))
    assert fee == 1


def test_kalshi_fee_cents_zero_at_extremes():
    # P=0 or P=1 -> P*(1-P)=0 -> fee is 0 regardless of contracts
    assert kalshi_fee_cents(1000, cents_to_pbp(100), Fraction(7, 100)) == 0


def test_kalshi_fee_cents_rejects_out_of_range_price():
    with pytest.raises(ValueError):
        kalshi_fee_cents(1, 20000, Fraction(7, 100))


def test_json_number_to_str_passes_through_strings():
    assert json_number_to_str("6888985.339440997") == "6888985.339440997"


def test_json_number_to_str_converts_float_exactly():
    assert json_number_to_str(0.022) == "0.022"
    assert json_number_to_str(6827071.013114997) == "6827071.013114997"


def test_json_number_to_str_converts_int():
    assert json_number_to_str(5) == "5"


def test_parse_dollars_to_cents_from_string_and_float():
    assert parse_dollars_to_cents("119143.12") == 11914312
    assert parse_dollars_to_cents(6827071.01) == 682707101


def test_parse_dollars_to_cents_floors_sub_cent_amounts():
    assert parse_dollars_to_cents("1.999") == 199


def test_spread_pbp_sign_and_magnitude():
    a = cents_to_pbp(60)
    b = cents_to_pbp(55)
    assert spread_pbp(a, b) == 500  # 5c gap = 500 pbp
    assert spread_pbp(b, a) == -500
