"""
Optional multi-value ("quote") parsing.

The heuristics in :mod:`price_parser.parser` return a single amount: the
first number found in the string. Real-world price blocks often contain
several numbers with different roles: a current price, a compare-at
(strikethrough / "was") price, a price range, a unit ("per item") or a
billing period ("per month"), plus unrelated numbers such as discount
thresholds, percentages, SKUs or model numbers.

:func:`parse_quote` is an opt-in, higher-level entry point which splits such
a block into structured components. It never changes the behavior of
``Price.fromstring``; instead it returns a :class:`Quote` which

* distinguishes the current price, the compare price, a price range,
  a unit and a period;
* keeps, for every value, the span of the original text it was extracted
  from and the source of its currency symbol ("text", "inherited" or
  "hint");
* reports ambiguities and ignored distractor numbers as ``diagnostics``
  instead of silently picking a number;
* exposes a ``price`` property returning a regular
  :class:`price_parser.Price`, with ``amount`` set to ``None`` when there
  is no single clear price (e.g. for ranges or ambiguous input).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TYPE_CHECKING

import attr

from .parser import (
    SAFE_CURRENCY_SYMBOLS,
    Price,
    extract_currency_symbol,
    or_regex,
    parse_number,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["PriceValue", "Quote", "parse_quote"]


@attr.s(auto_attribs=True)
class PriceValue:
    """A single price-like value found in the text."""

    amount: Decimal | None  # numeric value, as Decimal
    amount_text: str | None  # value as it appeared in the text
    currency: str | None  # currency symbol (as appeared in text)

    # Where the currency symbol comes from:
    # * "text"      - found next to this value,
    # * "inherited" - found next to another value in the same string,
    # * "hint"      - taken from the ``currency_hint`` argument,
    # * None        - no currency found.
    currency_source: str | None

    # (start, end) offsets of ``amount_text`` in the original string.
    # For values implied by a keyword (e.g. "free" -> 0) this is the span
    # of the keyword itself.
    span: tuple[int, int] | None

    # "current", "compare", "range_low" or "range_high"
    role: str = "current"


@attr.s(auto_attribs=True)
class Quote:
    """Structured result of parsing a price block."""

    current: PriceValue | None = None  # current (sale) price
    compare: PriceValue | None = None  # compare-at / "was" price
    range_low: PriceValue | None = None  # lower bound of a price range
    range_high: PriceValue | None = None  # upper bound of a price range
    unit: str | None = None  # raw unit text, e.g. "each", "每件"
    period: str | None = None  # raw period text, e.g. "/mo", "每月"
    diagnostics: list[str] = attr.ib(factory=list)

    # currency to fall back to when there is no current price; kept for
    # compatibility with Price.fromstring(currency_hint=...) behavior
    fallback_currency: str | None = attr.ib(default=None, repr=False)

    @property
    def values(self) -> list[PriceValue]:
        """All extracted values, in reading order of their roles."""
        return [
            v
            for v in (self.current, self.compare, self.range_low, self.range_high)
            if v is not None
        ]

    @property
    def price(self) -> Price:
        """
        This quote as a regular :class:`Price` instance.

        ``amount`` is only set when there is a single clear current price;
        it is ``None`` for ranges, ambiguous input and text without prices.
        """
        if self.current is not None:
            return Price(
                amount=self.current.amount,
                currency=self.current.currency,
                amount_text=self.current.amount_text,
            )
        currency = self.fallback_currency
        for v in (self.range_low, self.range_high, self.compare):
            if v is not None and v.currency:
                currency = v.currency
                break
        return Price(amount=None, currency=currency, amount_text=None)


# A number, possibly with thousand/decimal separators. Space-separated
# groups are only treated as thousand separators when they are groups of
# exactly 3 digits, so that "10 20" stays two numbers while "1 298,00"
# stays one.
_NUMBER_RE = re.compile(
    r"""
    \d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d{1,4})?  # 1 298,00
    |
    [.]?\d[\d.,']*                                # 1,298.00 / .75 / 50
    """,
    re.VERBOSE,
)

_CURRENCY_SYM_RE = or_regex(SAFE_CURRENCY_SYMBOLS)

# Range separators allowed between two numbers ("$10 - $20", "€10 – 20",
# "10 to 20", "10至20"). Currency symbols are stripped before matching.
_RANGE_SEP_RE = re.compile(r"[-–—~～]|to|至|до|bis|à", re.IGNORECASE)


def _words_regex(words: list[str]) -> re.Pattern[str]:
    """Regex matching any of ``words``; ASCII words require word boundaries."""
    parts = []
    for w in sorted(words, key=len, reverse=True):
        esc = re.escape(w)
        if w.isascii():
            pre = r"(?<![\w])" if w[0].isalnum() else ""
            post = r"(?![\w])" if w[-1].isalnum() else ""
            parts.append(pre + esc + post)
        else:
            parts.append(esc)
    return re.compile("|".join(parts), re.IGNORECASE)


_NOW_WORDS = [
    "now",
    "现价",
    "現在",
    "сейчас",
    "maintenant",
    "ahora",
    "jetzt",
    "adesso",
    "agora",
]
_WAS_WORDS = [
    "was",
    "rrp",
    "msrp",
    "list price",
    "regular price",
    "原价",
    "划线价",
    "было",
    "avant",
    "antes",
    "war",
    "prima",
]
_UNIT_WORDS = [
    "per item",
    "per piece",
    "per unit",
    "apiece",
    "each",
    "/item",
    "/each",
    "/pc",
    "每件",
    "每个",
    "pro stück",
    "par article",
    "por unidad",
    "por pieza",
    "за штуку",
    "al pezzo",
]
_PERIOD_WORDS = [
    "per month",
    "per year",
    "per week",
    "monthly",
    "annually",
    "yearly",
    "/month",
    "/year",
    "/week",
    "/mo",
    "/yr",
    "/wk",
    "每个月",
    "每月",
    "每年",
    "每周",
    "в месяц",
    "в год",
    "par mois",
    "par an",
    "al mes",
    "al año",
    "pro monat",
    "pro jahr",
    "al mese",
]
_FREE_WORDS = [
    "free",
    "gratuit",
    "gratis",
    "kostenlos",
    "бесплатно",
    "免费",
    "無料",
    "ฟรี",
]

_NOW_RE = _words_regex(_NOW_WORDS)
_WAS_RE = _words_regex(_WAS_WORDS)
_UNIT_RE = _words_regex(_UNIT_WORDS)
_PERIOD_RE = _words_regex(_PERIOD_WORDS)
_FREE_RE = _words_regex(_FREE_WORDS)

# Numbers which are not prices: identifiers, discount/saving amounts and
# threshold amounts ("满100减50"). Matched against the text right before
# the number.
_IDENTIFIER_RE = re.compile(
    r"(?<![a-z])(sku|mpn|upc|ean|isbn|model|mod|art|ref|货号|型号|款号|артикул)"
    r"\W{0,3}$",
    re.IGNORECASE,
)
_DISCOUNT_RE = re.compile(
    r"(?<![a-z])(save|off|减|省|скидка|descuento|remise|rabatt)\W{0,3}$",
    re.IGNORECASE,
)
_THRESHOLD_RE = re.compile(r"满\W{0,2}$")


@attr.s
class _Candidate:
    start: int = attr.ib()
    end: int = attr.ib()
    text: str = attr.ib()
    amount: Decimal | None = attr.ib()
    role: str | None = attr.ib(default=None)
    currency: str | None = attr.ib(default=None)
    currency_source: str | None = attr.ib(default=None)
    window: tuple[int, int] = attr.ib(default=(0, 0))

    def to_value(self) -> PriceValue:
        return PriceValue(
            amount=self.amount,
            amount_text=self.text,
            currency=self.currency,
            currency_source=self.currency_source,
            span=(self.start, self.end),
            role=self.role or "current",
        )


def _find_candidates(
    price: str,
    decimal_separator: str | None,
    digit_group_separator: str | None,
    diagnostics: list[str],
) -> list[_Candidate]:
    return list(
        _iter_candidates(price, decimal_separator, digit_group_separator, diagnostics)
    )


def _iter_candidates(
    price: str,
    decimal_separator: str | None,
    digit_group_separator: str | None,
    diagnostics: list[str],
) -> Iterator[_Candidate]:
    for m in _NUMBER_RE.finditer(price):
        start, end = m.start(), m.end()
        # strip trailing separators/spaces captured by the regex
        while end > start and price[end - 1] in ".,' ":
            end -= 1
        text = price[start:end]

        after = price[end:].lstrip()
        if after[:1] in ("%", "％", "折"):
            diagnostics.append(f"ignored_percent:{text}")
            continue
        before = price[max(0, start - 20) : start]
        if _IDENTIFIER_RE.search(before):
            diagnostics.append(f"ignored_identifier:{text}")
            continue
        if _THRESHOLD_RE.search(before):
            diagnostics.append(f"ignored_threshold:{text}")
            continue
        if _DISCOUNT_RE.search(before):
            diagnostics.append(f"ignored_discount:{text}")
            continue

        parse_text = text.replace("'", "")
        if digit_group_separator is not None:
            parse_text = parse_text.replace(digit_group_separator, "")
        amount = parse_number(parse_text, decimal_separator)
        if amount is None:
            continue
        yield _Candidate(start=start, end=end, text=text, amount=amount)


def _assign_windows(price: str, candidates: list[_Candidate]) -> None:
    for i, c in enumerate(candidates):
        prev_end = candidates[i - 1].end if i else 0
        next_start = candidates[i + 1].start if i + 1 < len(candidates) else len(price)
        c.window = (prev_end, next_start)


def _assign_roles(
    price: str, candidates: list[_Candidate], diagnostics: list[str]
) -> None:
    # 1. ranges: two consecutive numbers joined only by a range separator
    #    (and maybe currency symbols)
    i = 0
    while i + 1 < len(candidates):
        a, b = candidates[i], candidates[i + 1]
        between = _CURRENCY_SYM_RE.sub("", price[a.end : b.start]).strip()
        if between and _RANGE_SEP_RE.fullmatch(between):
            a.role = "range_low"
            b.role = "range_high"
            diagnostics.append("price_range")
            i += 2
        else:
            i += 1

    # 2. explicit now/was markers in the text preceding a number
    for c in candidates:
        if c.role is not None:
            continue
        before = price[c.window[0] : c.start]
        if _WAS_RE.search(before):
            c.role = "compare"
        elif _NOW_RE.search(before):
            c.role = "current"

    # keep at most one compare and one keyword-assigned current
    for role in ("compare", "current"):
        seen = False
        for c in candidates:
            if c.role == role:
                if seen:
                    c.role = None
                seen = True

    # 3. remaining numbers: prefer the one next to a currency symbol
    unassigned = [c for c in candidates if c.role is None]
    if unassigned and not any(c.role == "current" for c in candidates):
        anchored = [c for c in unassigned if _has_adjacent_currency(price, c)]
        if anchored:
            anchored[0].role = "current"
        elif len(unassigned) == 1:
            unassigned[0].role = "current"
        else:
            diagnostics.append("ambiguous_numbers")
    diagnostics.extend(
        f"unattributed_number:{c.text}" for c in candidates if c.role is None
    )


def _has_adjacent_currency(price: str, c: _Candidate) -> bool:
    before = price[max(0, c.start - 4) : c.start]
    after = price[c.end : c.end + 4]
    return bool(_CURRENCY_SYM_RE.search(before) or _CURRENCY_SYM_RE.search(after))


def _assign_currencies(
    price: str,
    candidates: list[_Candidate],
    currency_hint: str | None,
    diagnostics: list[str],
) -> str | None:
    for c in candidates:
        local = extract_currency_symbol(price[c.window[0] : c.window[1]], None)
        if local is not None:
            c.currency = local.strip()
            c.currency_source = "text"
    inherited = next((c.currency for c in candidates if c.currency), None)
    hint_currency = extract_currency_symbol(None, currency_hint)
    if hint_currency is not None:
        hint_currency = hint_currency.strip()
    for c in candidates:
        if c.currency is None:
            if inherited is not None:
                c.currency = inherited
                c.currency_source = "inherited"
            elif hint_currency is not None:
                c.currency = hint_currency
                c.currency_source = "hint"
    if candidates and inherited is None and hint_currency is None:
        diagnostics.append("no_currency")
    return inherited or hint_currency


def parse_quote(
    price: str | None,
    currency_hint: str | None = None,
    decimal_separator: str | None = None,
    digit_group_separator: str | None = None,
) -> Quote:
    """
    Parse a price block which may contain several price-like numbers, and
    return a :class:`Quote` with the current price, compare price, range,
    unit, period and diagnostics.

    Arguments are the same as for ``Price.fromstring``; unlike
    ``Price.fromstring``, the string itself is never modified, so all
    ``span`` offsets refer to the original ``price`` text
    (``digit_group_separator`` is applied to the parsed values only).
    """
    diagnostics: list[str] = []
    quote = Quote(diagnostics=diagnostics)
    if price is None:
        diagnostics.append("no_price_found")
        quote.fallback_currency = extract_currency_symbol(None, currency_hint)
        return quote

    candidates = _find_candidates(
        price, decimal_separator, digit_group_separator, diagnostics
    )

    if not candidates:
        m = _FREE_RE.search(price)
        if m:
            currency = extract_currency_symbol(price, currency_hint)
            if currency is not None:
                currency = currency.strip()
            source = (
                "text"
                if extract_currency_symbol(price, None) is not None
                else ("hint" if currency is not None else None)
            )
            quote.current = PriceValue(
                amount=Decimal(0),
                amount_text="0",
                currency=currency,
                currency_source=source,
                span=(m.start(), m.end()),
                role="current",
            )
        else:
            diagnostics.append("no_price_found")
            quote.fallback_currency = extract_currency_symbol(None, currency_hint)
        return quote

    _assign_windows(price, candidates)
    _assign_roles(price, candidates, diagnostics)
    fallback = _assign_currencies(price, candidates, currency_hint, diagnostics)
    quote.fallback_currency = fallback

    for c in candidates:
        value = c.to_value()
        if c.role == "current" and quote.current is None:
            quote.current = value
        elif c.role == "compare" and quote.compare is None:
            quote.compare = value
        elif c.role == "range_low" and quote.range_low is None:
            quote.range_low = value
        elif c.role == "range_high" and quote.range_high is None:
            quote.range_high = value

    if quote.compare is not None and quote.current is None:
        diagnostics.append("compare_without_current")

    m = _UNIT_RE.search(price)
    if m:
        quote.unit = m.group(0)
    m = _PERIOD_RE.search(price)
    if m:
        quote.period = m.group(0)

    return quote
