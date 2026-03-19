"""
Money value object.

All financial amounts in U-FirstSupport are integers in minor currency units
(e.g. 5000 = £50.00 / ₦5000 / $50.00 depending on currency).

Using int everywhere prevents floating-point rounding errors in financial
calculations. Never use float for money — see CLAUDE.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


# ISO 4217 minor-unit exponents for supported currencies
_MINOR_UNITS: dict[str, int] = {
    "GBP": 2,
    "EUR": 2,
    "USD": 2,
    "NGN": 2,
    "GHS": 2,
    "KES": 2,
    "ZAR": 2,
    "CAD": 2,
}


@dataclass(frozen=True)
class Money:
    amount: int      # always in minor units
    currency: str    # ISO 4217 uppercase

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int):
            raise TypeError(
                f"Money.amount must be int, got {type(self.amount).__name__}. "
                "Never use float for money."
            )
        if self.currency not in _MINOR_UNITS:
            raise ValueError(f"Unsupported currency: {self.currency}")

    # ------------------------------------------------------------------
    # Arithmetic — both operands must share the same currency
    # ------------------------------------------------------------------

    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    @property
    def exponent(self) -> int:
        return _MINOR_UNITS[self.currency]

    def as_decimal(self) -> Decimal:
        """Return the major-unit Decimal value (for display or logging only)."""
        factor = Decimal(10) ** self.exponent
        return (Decimal(self.amount) / factor).quantize(
            Decimal(10) ** -self.exponent, rounding=ROUND_HALF_UP
        )

    def __str__(self) -> str:
        return f"{self.currency} {self.as_decimal()}"

    def __repr__(self) -> str:
        return f"Money(amount={self.amount}, currency={self.currency!r})"

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_decimal(cls, value: Decimal, currency: str) -> Money:
        """
        Convert a Decimal major-unit amount to Money.

        Use only at system boundaries (e.g. parsing bank statements).
        Internally always work with the int minor-unit form.
        """
        exponent = _MINOR_UNITS[currency]
        minor = value * Decimal(10) ** exponent
        return cls(int(minor.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), currency)

    # ------------------------------------------------------------------
    # Pydantic-compatible dict for API serialisation
    # ------------------------------------------------------------------

    def to_api(self) -> dict[str, int | str]:
        return {"amount": self.amount, "currency": self.currency}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot operate on different currencies: {self.currency} vs {other.currency}"
            )
