from arb.money import cents_to_pbp
from arb.polarity import Polarity, flip, to_common_yes_price


def test_aligned_passthrough():
    p = cents_to_pbp(63)
    assert to_common_yes_price(p, Polarity.ALIGNED) == p


def test_inverted_complements():
    p = cents_to_pbp(63)
    assert to_common_yes_price(p, Polarity.INVERTED) == cents_to_pbp(37)


def test_inverted_is_involution():
    p = cents_to_pbp(20)
    once = to_common_yes_price(p, Polarity.INVERTED)
    twice = to_common_yes_price(once, Polarity.INVERTED)
    assert twice == p


def test_flip():
    assert flip(Polarity.ALIGNED) is Polarity.INVERTED
    assert flip(Polarity.INVERTED) is Polarity.ALIGNED


def test_edges():
    assert to_common_yes_price(cents_to_pbp(0), Polarity.INVERTED) == cents_to_pbp(100)
    assert to_common_yes_price(cents_to_pbp(100), Polarity.INVERTED) == cents_to_pbp(0)
