"""Stable byte encodings and identifiers used by the SFEP producer."""

from __future__ import annotations

import hashlib
import json
import math
import operator
from collections.abc import Mapping, Sequence
from fractions import Fraction
from numbers import Integral, Real


_MIN_DECIMAL_EXPONENT = -324
_MAX_DECIMAL_EXPONENT = 308
_MAX_BINARY64_SIGNIFICAND_DIGITS = 17


def _scaled_floor(value: Fraction, exponent: int) -> int:
    """Return floor(value / 10**exponent) without a lossy decimal conversion."""
    if exponent >= 0:
        return value.numerator // (value.denominator * (10**exponent))
    return (value.numerator * (10 ** (-exponent))) // value.denominator


def _rounding_interval(number: float) -> tuple[Fraction, Fraction]:
    """Return decimal values that can round to this positive binary64 value."""
    target = Fraction.from_float(number)
    previous = Fraction.from_float(math.nextafter(number, -math.inf))
    lower = (previous + target) / 2

    following_float = math.nextafter(number, math.inf)
    if math.isinf(following_float):
        # Infinity begins half a final-ULP above the largest finite binary64.
        upper = target + (target - previous) / 2
    else:
        upper = (target + Fraction.from_float(following_float)) / 2
    return lower, upper


def _decimal_spellings(coefficient: int, exponent: int) -> set[str]:
    """Return the non-redundant fixed/scientific spellings of coefficient*10**exponent."""
    digits = str(coefficient)
    if exponent >= 0:
        fixed = digits + ("0" * exponent)
    else:
        point = len(digits) + exponent
        fixed = (
            digits[:point] + "." + digits[point:]
            if point > 0
            else "0." + ("0" * (-point)) + digits
        )

    spellings = {fixed}
    for point in range(1, len(digits) + 1):
        mantissa = digits if point == len(digits) else digits[:point] + "." + digits[point:]
        spellings.add(f"{mantissa}e{exponent + len(digits) - point}")
    return spellings


def _canonical_float(number: float) -> str:
    """Encode a finite binary64 using the globally shortest valid JSON number."""
    if not math.isfinite(number):
        raise ValueError("canonical JSON numbers must be finite")
    if number == 0.0:
        return "0"

    negative = number < 0.0
    magnitude = -number if negative else number
    lower, upper = _rounding_interval(magnitude)
    prefix = "-" if negative else ""
    candidates: set[str] = set()

    for digits_count in range(1, _MAX_BINARY64_SIGNIFICAND_DIGITS + 1):
        least = 1 if digits_count == 1 else 10 ** (digits_count - 1)
        greatest = (10**digits_count) - 1
        for exponent in range(_MIN_DECIMAL_EXPONENT, _MAX_DECIMAL_EXPONENT + 1):
            # The extra integer at each edge accounts for an exact midpoint;
            # float() below applies the platform's binary64 tie rule exactly.
            first = max(least, _scaled_floor(lower, exponent) - 1)
            last = min(greatest, _scaled_floor(upper, exponent) + 1)
            for coefficient in range(first, last + 1):
                if coefficient % 10 == 0:
                    continue
                for spelling in _decimal_spellings(coefficient, exponent):
                    candidate = prefix + spelling
                    if float(candidate) == number:
                        candidates.add(candidate)

    if not candidates:  # Every finite binary64 has a <=17-digit spelling.
        raise AssertionError("no canonical decimal spelling found for finite binary64")
    return min(
        candidates,
        key=lambda candidate: (len(candidate.encode("utf-8")), candidate.encode("utf-8")),
    )


def _normalise_real(value: Real, label: str) -> float:
    """Convert an accepted real scalar exactly once into a built-in binary64."""
    if isinstance(value, bool):
        raise TypeError(f"{label} must not be boolean")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _normalise_integer(value: Integral) -> int:
    """Convert a non-built-in integral scalar once while retaining its exact integer value."""
    return int(operator.index(value))


def _snapshot_mapping_entries(value: Mapping[object, object]) -> tuple[tuple[object, object], ...]:
    """Read Mapping.items() once so validation and serialization use identical entries."""
    try:
        return tuple((key, item) for key, item in value.items())
    except (TypeError, ValueError) as error:
        raise TypeError("mapping items must be key-value pairs") from error


def _canonical_json_text(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, int):
        raise TypeError("canonical JSON does not accept integer subclasses")
    if type(value) is float:
        return _canonical_float(value)
    if isinstance(value, Integral):
        return str(_normalise_integer(value))
    if isinstance(value, Real):
        return _canonical_float(_normalise_real(value, "canonical JSON number"))
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str):
        raise TypeError("canonical JSON does not accept string subclasses")
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json_text(item) for item in value) + "]"
    if isinstance(value, Mapping):
        entries = _snapshot_mapping_entries(value)
        seen_keys: set[str] = set()
        checked_entries: list[tuple[str, object]] = []
        for key, item in entries:
            if type(key) is not str:
                raise TypeError("canonical JSON object keys must be string keys")
            if key in seen_keys:
                raise TypeError("canonical JSON object keys must be unique")
            seen_keys.add(key)
            checked_entries.append((key, item))
        return "{" + ",".join(
            _canonical_json_text(key) + ":" + _canonical_json_text(item)
            for key, item in sorted(checked_entries, key=lambda entry: entry[0])
        ) + "}"
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Return canonical UTF-8 JSON, sorted by object key, with one final LF.

    Built-in ints are emitted exactly. Other ``numbers.Integral`` values are
    normalized to exact built-in ints; other ``numbers.Real`` values are
    normalized once to finite binary64. String and int subclasses are rejected
    so polymorphic ``str``/``repr`` hooks cannot affect emitted bytes.
    """
    return (_canonical_json_text(value) + "\n").encode("utf-8")


def sha256_uri(data: bytes) -> str:
    """Return a SHA-256 digest in the normative URI-like representation."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _snapshot_id_component(component: object, label: str) -> str:
    if type(component) is not str:
        raise TypeError(f"{label} must be a built-in string")
    if any(character in component for character in ("=", "\r", "\n")):
        raise ValueError(f"{label} must not contain '=', CR, or LF")
    return component


def id_lines(version: str, fields: Mapping[str, str]) -> bytes:
    """Return a stable line-oriented ID preimage from a single fields snapshot."""
    version_snapshot = _snapshot_id_component(version, "version")
    entries = _snapshot_mapping_entries(fields)
    checked_entries: list[tuple[str, str]] = []
    seen_keys: set[str] = set()
    for key, value in entries:
        key_snapshot = _snapshot_id_component(key, "field key")
        value_snapshot = _snapshot_id_component(value, "field value")
        if key_snapshot in seen_keys:
            raise ValueError("field keys must be unique")
        seen_keys.add(key_snapshot)
        checked_entries.append((key_snapshot, value_snapshot))
    lines = [version_snapshot, *(
        f"{key}={value}"
        for key, value in sorted(checked_entries, key=lambda entry: entry[0].encode("utf-8"))
    )]
    return ("\n".join(lines) + "\n").encode("utf-8")


def digest_json_id(namespace: str, value: Mapping[str, object]) -> str:
    """Hash a canonical JSON object under a newline-separated namespace."""
    if type(namespace) is not str:
        raise TypeError("namespace must be a built-in string")
    preimage = namespace.encode("utf-8") + b"\n" + canonical_json_bytes(value)
    return sha256_uri(preimage)


def type1_quantile(values: Sequence[float], q: float) -> float | None:
    """Return the inverse empirical distribution quantile as a built-in float."""
    q_number = _normalise_real(q, "q")
    if not 0 < q_number <= 1:
        raise ValueError("q must satisfy 0 < q <= 1")
    values_snapshot = tuple(values)
    if not values_snapshot:
        return None
    ordered = sorted(_normalise_real(value, "quantile values") for value in values_snapshot)
    index = max(0, min(len(ordered) - 1, math.ceil(q_number * len(ordered)) - 1))
    return float(ordered[index])
