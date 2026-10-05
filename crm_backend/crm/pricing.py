"""Quote money maths. One pure function so the API, the PDF and the tests can't disagree."""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Totals:
    line_totals: list
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    total: Decimal


def calculate(items, discount_type="percent", discount_value=0, tax_percent=0) -> Totals:
    """items: iterable of (quantity, unit_price). Discount is applied before tax; it can never exceed the subtotal."""
    lines = [money(Decimal(q) * Decimal(p)) for q, p in items]
    subtotal = sum(lines, Decimal("0"))
    dv = Decimal(discount_value or 0)
    discount = money(subtotal * dv / 100) if discount_type == "percent" else money(dv)
    discount = min(discount, subtotal)
    taxable = subtotal - discount
    tax = money(taxable * Decimal(tax_percent or 0) / 100)
    return Totals(lines, money(subtotal), money(discount), tax, money(taxable + tax))


def apply(quote, items=None):
    """Recompute and set totals on `quote` and its items (does not save). Pass `items` (QuoteItem list) or it reads the DB."""
    items = list(quote.items.all()) if items is None else items
    t = calculate([(i.quantity, i.unit_price) for i in items], quote.discount_type, quote.discount_value, quote.tax_percent)
    for item, lt in zip(items, t.line_totals):
        item.line_total = lt
    quote.subtotal, quote.discount_amount, quote.tax_amount, quote.total = t.subtotal, t.discount_amount, t.tax_amount, t.total
    return t


SYMBOL = {"INR": "₹", "USD": "$", "GBP": "£", "EUR": "€", "AED": "AED "}


def format_money(amount, currency="INR", symbol=None) -> str:
    """Indian lakh/crore grouping for INR (12,34,567.00), western grouping otherwise."""
    amount = money(amount)
    neg = amount < 0
    whole, frac = f"{abs(amount):.2f}".split(".")
    if currency == "INR" and len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    else:
        whole = f"{int(whole):,}"
    sym = symbol if symbol is not None else SYMBOL.get(currency, currency + " ")
    return f"{'-' if neg else ''}{sym}{whole}.{frac}"
