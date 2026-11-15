"""
Tests for the optional quote parsing entry point (price_parser.parse_quote).

The samples are fixed multilingual price blocks covering: Now/Was markers,
price ranges, per-unit and per-period suffixes, free items and distractor
numbers (discounts, thresholds, SKUs, model numbers).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from price_parser import Price, PriceValue, Quote, parse_quote


def test_now_was_en() -> None:
    q = parse_quote("Was $49.99, Now $29.99")
    assert q.current is not None
    assert q.current.amount == Decimal("29.99")
    assert q.current.amount_text == "29.99"
    assert q.current.currency == "$"
    assert q.current.currency_source == "text"
    assert q.compare is not None
    assert q.compare.amount == Decimal("49.99")
    assert q.compare.currency == "$"
    assert q.range_low is None
    assert q.range_high is None
    # spans point into the original string
    s = "Was $49.99, Now $29.99"
    assert q.current.span is not None
    assert s[slice(*q.current.span)] == "29.99"
    assert q.compare.span is not None
    assert s[slice(*q.compare.span)] == "49.99"
    # compatible Price view
    assert q.price == Price(amount=Decimal("29.99"), currency="$", amount_text="29.99")


def test_now_was_zh() -> None:
    q = parse_quote("现价 ¥199，原价 ¥299")
    assert q.current is not None
    assert q.current.amount == Decimal(199)
    assert q.current.currency == "¥"
    assert q.compare is not None
    assert q.compare.amount == Decimal(299)
    assert q.compare.currency == "¥"


def test_now_was_ru_space_grouped() -> None:
    q = parse_quote("Было 1 299 ₽, сейчас 999 ₽")
    assert q.current is not None
    assert q.current.amount == Decimal(999)
    assert q.compare is not None
    assert q.compare.amount == Decimal(1299)
    assert q.compare.amount_text == "1 299"
    assert q.compare.currency == "₽"


def test_was_without_now_keyword() -> None:
    q = parse_quote("Was $49.99 Now 29.99")
    assert q.compare is not None
    assert q.compare.amount == Decimal("49.99")
    assert q.current is not None
    assert q.current.amount == Decimal("29.99")
    # currency inherited from the compare price in the same string
    assert q.current.currency == "$"
    assert q.current.currency_source == "inherited"
    assert q.compare.currency_source == "text"


@pytest.mark.parametrize(
    ("text", "low", "high"),
    [
        ("€10 – €20", "10", "20"),
        ("$10 to $20", "10", "20"),
        ("¥100～¥200", "100", "200"),
        ("10-20 USD", "10", "20"),
    ],
)
def test_ranges(text: str, low: str, high: str) -> None:
    q = parse_quote(text)
    assert q.range_low is not None
    assert q.range_low.amount == Decimal(low)
    assert q.range_high is not None
    assert q.range_high.amount == Decimal(high)
    assert q.current is None
    # a range is not a single clear price
    assert q.price.amount is None
    assert "price_range" in q.diagnostics


def test_range_keeps_spans_and_currency() -> None:
    s = "€10 – €20"
    q = parse_quote(s)
    assert q.range_low is not None
    assert q.range_low.span is not None
    assert q.range_high is not None
    assert q.range_high.span is not None
    assert s[slice(*q.range_low.span)] == "10"
    assert s[slice(*q.range_high.span)] == "20"
    assert q.range_low.currency == "€"
    assert q.range_high.currency == "€"
    assert q.price.currency == "€"


@pytest.mark.parametrize(
    ("text", "unit"),
    [
        ("$5.99 each", "each"),
        ("$5.99 per item", "per item"),
        ("¥30 每件", "每件"),
        ("29,90 € pro Stück", "pro Stück"),
    ],
)
def test_units(text: str, unit: str) -> None:
    q = parse_quote(text)
    assert q.unit == unit
    assert q.current is not None
    assert q.price.amount is not None


@pytest.mark.parametrize(
    ("text", "period"),
    [
        ("$9.99/month", "/month"),
        ("$9.99/mo", "/mo"),
        ("$9.99 per month", "per month"),
        ("每月 ¥30", "每月"),
        ("30 € par mois", "par mois"),
        ("999 ₽ в месяц", "в месяц"),
    ],
)
def test_periods(text: str, period: str) -> None:
    q = parse_quote(text)
    assert q.period == period
    assert q.current is not None
    assert q.price.amount is not None


@pytest.mark.parametrize("text", ["Free", "免费", "Gratuit", "Бесплатно"])
def test_free(text: str) -> None:
    q = parse_quote(text)
    assert q.current is not None
    assert q.current.amount == Decimal(0)
    assert q.current.amount_text == "0"
    assert q.price.amount == Decimal(0)


def test_free_span_covers_keyword() -> None:
    q = parse_quote("免费送货")
    assert q.current is not None
    assert q.current.span is not None
    assert "免费送货"[slice(*q.current.span)] == "免费"


def test_distractor_percent() -> None:
    q = parse_quote("Save 50% — now $80")
    assert q.current is not None
    assert q.current.amount == Decimal(80)
    assert "ignored_percent:50" in q.diagnostics


def test_distractor_model_number() -> None:
    q = parse_quote("iPhone 15 Pro $999")
    assert q.current is not None
    assert q.current.amount == Decimal(999)
    assert "unattributed_number:15" in q.diagnostics


def test_distractor_sku() -> None:
    q = parse_quote("SKU 847291, $19.99")
    assert q.current is not None
    assert q.current.amount == Decimal("19.99")
    assert "ignored_identifier:847291" in q.diagnostics


def test_distractor_threshold_and_discount_zh() -> None:
    q = parse_quote("满100减50，现价 ¥80")
    assert q.current is not None
    assert q.current.amount == Decimal(80)
    assert q.current.currency == "¥"
    assert "ignored_threshold:100" in q.diagnostics
    assert "ignored_discount:50" in q.diagnostics


def test_ambiguous_numbers_have_no_amount() -> None:
    q = parse_quote("10 20")
    assert q.current is None
    assert q.price.amount is None
    assert "ambiguous_numbers" in q.diagnostics
    assert "no_currency" in q.diagnostics


def test_no_price_keeps_amount_empty() -> None:
    q = parse_quote("Out of stock")
    assert q.current is None
    assert q.price.amount is None
    assert q.price.amount_text is None
    assert "no_price_found" in q.diagnostics


def test_none_input() -> None:
    q = parse_quote(None, currency_hint="$")
    assert q.price.amount is None
    assert q.price.currency == "$"
    assert "no_price_found" in q.diagnostics


def test_currency_hint_used_when_text_has_none() -> None:
    q = parse_quote("Now 29.99", currency_hint="USD")
    assert q.current is not None
    assert q.current.amount == Decimal("29.99")
    assert q.current.currency == "USD"
    assert q.current.currency_source == "hint"


def test_compare_without_current_diagnostic() -> None:
    q = parse_quote("Was $49.99")
    assert q.compare is not None
    assert q.current is None
    assert q.price.amount is None
    assert "compare_without_current" in q.diagnostics


def test_decimal_separator_argument() -> None:
    q = parse_quote("Now 140.000", decimal_separator=",")
    assert q.current is not None
    assert q.current.amount == Decimal(140000)


def test_digit_group_separator_argument_keeps_spans() -> None:
    s = "Now $1.299,00"
    q = parse_quote(s, digit_group_separator=".")
    assert q.current is not None
    assert q.current.amount == Decimal("1299.00")
    # span still refers to the original, unmodified string
    assert q.current.span is not None
    assert s[slice(*q.current.span)] == "1.299,00"


def test_quote_values_and_types() -> None:
    q = parse_quote("Was $49.99, Now $29.99")
    assert isinstance(q, Quote)
    assert all(isinstance(v, PriceValue) for v in q.values)
    assert {v.role for v in q.values} == {"current", "compare"}
    assert isinstance(q.price, Price)


def test_existing_price_entry_points_unchanged() -> None:
    # the heuristic entry point keeps its historical behavior
    assert Price.fromstring("22,90 €") == Price(
        amount=Decimal("22.90"), currency="€", amount_text="22,90"
    )
    # ... while the quote parser attaches structure on top
    q = parse_quote("22,90 €")
    assert q.price == Price.fromstring("22,90 €")
