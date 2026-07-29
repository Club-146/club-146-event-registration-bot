"""One guest-pricing rule, asserted identically in both repos.

Decided by Petr, 27 Jul 2026: **a guest who gives a school year is priced
exactly like a registrant of that year; a guest with no school year pays the
flat guest rate.**

Before this, the website charged every guest a flat `guest_price_fixed` while
the bot priced guests by their own graduation year. The same person cost 1500
as a website guest and 7600 as a bot guest, so registering a classmate as your
"guest" was a 6100₽ discount on registering as yourself.

THE TABLE BELOW IS SHARED. Its twin lives at
`146.school/newsite/tests/test_guest_pricing_parity.py`. This side is the
canonical implementation -- `App.calculate_guest_price` -- and the website was
changed to match it. If you change guest pricing in either repo, both copies
must be updated together or one of the two suites fails; that is the entire
point of duplicating the table rather than importing it. A shared fixture would
be nicer but the two services have separate venvs and no shared package.

The rule needs **no code change on this side**. It only requires
`guest_price_minimum` to be set to the intended flat guest rate (1500), because
that single field serves as both the floor for alum guests and the price for a
«друг». With it at 0, a «друг» falls through to "the formula for someone who
left 15 years ago" (4400 for the Aug 1 config), which is not the intent.
"""

from __future__ import annotations

import pytest

from src.app import App

# (graduation_year | None, early_bird_active) -> expected amount in rubles.
#
# Config: base 1400, rate 200, reference year 2026, step 1,
#         guest_price_minimum = 1500, early_bird_discount 500,
#         free_for_types ["TEACHER"].
CANONICAL_GUEST_PRICES = {
    # a guest who is an alum pays what that alum would pay
    (2026, False): 1500,  # 1400 formula, floored up to the 1500 minimum
    (2024, False): 1800,
    (2010, False): 4600,
    (1995, False): 7600,
    # ...and the early-bird discount comes off after the floor
    (2026, True): 1000,  # floored to 1500, then -500
    (2024, True): 1300,
    (2010, True): 4100,
    (1995, True): 7100,
    # no school year -> the flat guest rate, discount still applies
    (None, False): 1500,
    (None, True): 1000,
}


class _Shim:
    """`calculate_guest_price` only needs its sibling method, not a live bot."""

    calculate_event_payment = App.calculate_event_payment
    calculate_guest_price = App.calculate_guest_price


def _event(*, early_bird: bool) -> dict:
    from datetime import datetime, timedelta

    event = {
        "_id": "6a599a17a37724d81b7eadc3",
        "pricing_type": "formula",
        "price_formula_base": 1400,
        "price_formula_rate": 200,
        "price_formula_reference_year": 2026,
        "price_formula_step": 1,
        # the flat guest rate AND the floor for alum guests -- one field
        "guest_price_minimum": 1500,
        "free_for_types": ["TEACHER"],
        "early_bird_discount": 500 if early_bird else 0,
    }
    if early_bird:
        # is_early_bird_active falls back to D-3 of the event date
        event["date"] = datetime.now() + timedelta(days=10)
    return event


@pytest.mark.parametrize(
    "key,expected",
    sorted(
        CANONICAL_GUEST_PRICES.items(), key=lambda item: (item[0][0] or 0, item[0][1])
    ),
)
def test_matches_the_canonical_table(key, expected):
    year, early_bird = key
    app = _Shim()
    # A guest with no school year is the NON_GRADUATE branch.
    graduate_type = "GRADUATE" if year is not None else "NON_GRADUATE"
    regular, discounted = app.calculate_guest_price(
        _event(early_bird=early_bird),
        year if year is not None else 2011,
        graduate_type,
    )
    assert (discounted if early_bird else regular) == expected


def test_an_alum_guest_is_not_cheaper_than_registering_as_themselves():
    """The loophole this rule closes."""
    app = _Shim()
    event = _event(early_bird=False)
    as_guest, _ = app.calculate_guest_price(event, 1995, "GRADUATE")
    as_registrant, _, _, _ = app.calculate_event_payment(event, 1995, "GRADUATE")
    assert as_guest == as_registrant == 7600


def test_a_free_type_guest_pays_nothing_and_is_not_floored_up():
    """Free types short-circuit to 0 *before* the minimum applies -- otherwise a
    teacher brought as a guest would be charged the 1500 floor."""
    app = _Shim()
    assert app.calculate_guest_price(_event(early_bird=False), 2010, "TEACHER") == (
        0,
        0,
    )


def test_a_zero_minimum_does_not_give_the_intended_flat_rate():
    """Documents why the config must set guest_price_minimum=1500.

    With it at 0 a «друг» falls through to the 15-years-ago formula, which is
    4400 -- not the 1500 the flat rate is meant to be. This is a config
    requirement, not a code path, so it is pinned here.
    """
    app = _Shim()
    event = _event(early_bird=False)
    event["guest_price_minimum"] = 0
    regular, _ = app.calculate_guest_price(event, 2011, "NON_GRADUATE")
    assert regular == 4400
    assert regular != CANONICAL_GUEST_PRICES[(None, False)]
