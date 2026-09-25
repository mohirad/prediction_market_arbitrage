from arb.money import cents_to_pbp
from arb.fees import (
    kalshi_maker_fee_cents,
    kalshi_taker_fee_cents,
    polymarket_maker_fee_cents,
    polymarket_taker_fee_cents,
)


def test_kalshi_taker_fee_default_multiplier():
    # 100 contracts @ 50c, default 0.07 multiplier -> ceil(0.07*100*0.25*100)=175c
    assert kalshi_taker_fee_cents(100, cents_to_pbp(50)) == 175


def test_kalshi_taker_fee_index_series_halved():
    normal = kalshi_taker_fee_cents(100, cents_to_pbp(50), series_ticker="KXHIGHNY")
    index = kalshi_taker_fee_cents(100, cents_to_pbp(50), series_ticker="INX")
    assert index < normal
    assert index == 88  # ceil(0.035*100*0.25*100) = ceil(87.5) = 88


def test_kalshi_maker_fee_is_zero():
    assert kalshi_maker_fee_cents(1000, cents_to_pbp(50)) == 0


def test_polymarket_taker_fee_uses_market_bps_when_given():
    # 100 shares @ 50c, taker_base_fee_bps=700 (7%) -> same as kalshi 0.07 case = 175c
    fee = polymarket_taker_fee_cents(100, cents_to_pbp(50), taker_base_fee_bps=700)
    assert fee == 175


def test_polymarket_taker_fee_falls_back_to_category_table():
    crypto_fee = polymarket_taker_fee_cents(100, cents_to_pbp(50), category="crypto")
    geo_fee = polymarket_taker_fee_cents(100, cents_to_pbp(50), category="geopolitics")
    assert crypto_fee == 175
    assert geo_fee == 0


def test_polymarket_taker_fee_unknown_category_uses_default():
    fee = polymarket_taker_fee_cents(100, cents_to_pbp(50), category="some-new-category")
    default_fee = polymarket_taker_fee_cents(100, cents_to_pbp(50), category=None)
    assert fee == default_fee


def test_polymarket_maker_fee_is_zero():
    assert polymarket_maker_fee_cents(500, cents_to_pbp(50)) == 0
