"""Stable byte encodings and identifiers used by the SFEP producer."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence


def _canonical_float(number: float) -> str:
    """Encode a finite binary64 using the shortest valid decimal spelling."""
    if not math.isfinite(number):
        raise ValueError("canonical JSON numbers must be finite")
    if number == 0.0:
        return "0"

    negative = number < 0.0
    coefficient, separator, exponent_text = repr(abs(number)).lower().partition("e")
    exponent = int(exponent_text) if separator else 0
    integer, dot, fraction = coefficient.partition(".")
    digits = (integer + fraction).lstrip("0")
    decimal_exponent = exponent - (len(fraction) if dot else 0)
    while digits.endswith("0"):
        digits = digits[:-1]
        decimal_exponent += 1

    if decimal_exponent >= 0:
        fixed = digits + ("0" * decimal_exponent)
    else:
        point = len(digits) + decimal_exponent
        fixed = (
            digits[:point] + "." + digits[point:]
            if point > 0
            else "0." + ("0" * -point) + digits
        )

    candidates = {fixed}
    for point in range(1, len(digits) + 1):
        mantissa = digits if point == len(digits) else digits[:point] + "." + digits[point:]
        candidates.add(f"{mantissa}e{decimal_exponent + len(digits) - point}")

    prefix = "-" if negative else ""
    equivalent = [
        prefix + candidate
        for candidate in candidates
        if math.isfinite(float(prefix + candidate))
        and float(prefix + candidate) == number
    ]
    return min(equivalent, key=lambda candidate: (len(candidate.encode("utf-8")), candidate.encode("utf-8")))


def _canonical_json_text(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json_text(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be string keys")
        return "{" + ",".join(
            _canonical_json_text(key) + ":" + _canonical_json_text(value[key])
            for key in sorted(value)
        ) + "}"
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Return canonical UTF-8 JSON, sorted by object key, with one final LF."""
    return (_canonical_json_text(value) + "\n").encode("utf-8")


def sha256_uri(data: bytes) -> str:
    """Return a SHA-256 digest in the normative URI-like representation."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _validate_id_component(component: str, label: str) -> None:
    if not isinstance(component, str):
        raise TypeError(f"{label} must be a string")
    if any(character in component for character in ("=", "\r", "\n")):
        raise ValueError(f"{label} must not contain '=', CR, or LF")


def id_lines(version: str, fields: Mapping[str, str]) -> bytes:
    """Return a stable line-oriented ID preimage."""
    _validate_id_component(version, "version")
    for key, value in fields.items():
        _validate_id_component(key, "field key")
        _validate_id_component(value, "field value")
    lines = [version, *(
        f"{key}={fields[key]}" for key in sorted(fields, key=lambda key: key.encode("utf-8"))
    )]
    return ("\n".join(lines) + "\n").encode("utf-8")


def digest_json_id(namespace: str, value: Mapping[str, object]) -> str:
    """Hash a canonical JSON object under a newline-separated namespace."""
    preimage = namespace.encode("utf-8") + b"\n" + canonical_json_bytes(value)
    return sha256_uri(preimage)


def type1_quantile(values: Sequence[float], q: float) -> float | None:
    """Return the inverse empirical distribution quantile for finite samples."""
    if not math.isfinite(q) or not 0 < q <= 1:
        raise ValueError("q must be finite and satisfy 0 < q <= 1")
    if not values:
        return None
    if not all(math.isfinite(value) for value in values):
        raise ValueError("quantile values must be finite")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[index]
