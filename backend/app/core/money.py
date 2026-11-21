from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def decimal_inr_to_paise(amount: Decimal | int | float | str) -> int:
    """Convert an INR decimal/numeric amount to integer paise (1 INR = 100 paise)."""
    try:
        dec = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid INR amount: {amount!r}") from exc

    if not dec.is_finite():
        raise ValueError(f"INR amount must be finite: {amount!r}")
    if dec < Decimal("0"):
        raise ValueError(f"INR amount cannot be negative: {amount!r}")

    paise = (dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(paise)


def paise_to_inr_display(paise: int, *, include_symbol: bool = True) -> str:
    """Format integer paise as an INR display string (e.g. '₹123.45')."""
    sign = "-" if paise < 0 else ""
    amount = (Decimal(abs(paise)) / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    prefix = "₹" if include_symbol else ""
    return f"{sign}{prefix}{amount:.2f}"
