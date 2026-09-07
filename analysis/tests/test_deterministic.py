from __future__ import annotations

import json
import math
import random
import struct
import time
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from equipment_quality.deterministic import (
    canonical_json_bytes,
    digest_json_id,
    id_lines,
    sha256_uri,
    type1_quantile,
)


NUMBER_VECTORS = json.loads(
    Path("contracts/equipment-monitor/v1/canonical-number-test-vectors.json").read_text(
        encoding="utf-8"
    )
)


def test_canonical_json_is_compact_sorted_utf8_and_lf_terminated():
    assert canonical_json_bytes({"한글": "값", "a": 1.5}) == (
        '{"a":1.5,"한글":"값"}\n'.encode("utf-8")
    )


@pytest.mark.parametrize("vector", NUMBER_VECTORS, ids=lambda vector: vector["name"])
def test_canonical_json_consumes_every_approved_finite_binary64_vector(vector):
    value = float.fromhex(vector["hex"])

    assert canonical_json_bytes({"number": value}) == (
        f'{{"number":{vector["expected"]}}}\n'.encode("utf-8")
    )


def test_canonical_json_uses_global_lexicographic_tie_break_for_smallest_normal():
    smallest_normal = float.fromhex("0x1.0000000000000p-1022")

    assert canonical_json_bytes(smallest_normal) == b"22250738585072012e-324\n"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (float.fromhex("0x1.6345785d8a03fp+56"), b"100000000000001001\n"),
        (-float.fromhex("0x1.6345785d8a03fp+56"), b"-100000000000001001\n"),
    ],
)
def test_canonical_json_uses_equal_length_18_digit_lexicographic_alternative(value, expected):
    assert canonical_json_bytes(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (float(2**53), b"9007199254740992\n"),
        (-float(2**53), b"-9007199254740992\n"),
        (float(2**54), b"18014398509481983\n"),
        (-float(2**54), b"-18014398509481983\n"),
        (float.fromhex("0x1.0000000000001p+54"), b"18014398509481987\n"),
        (-float.fromhex("0x1.0000000000001p+54"), b"-18014398509481987\n"),
    ],
)
def test_canonical_json_uses_integral_float_rounding_interval_and_ties(value, expected):
    assert canonical_json_bytes(value) == expected


def test_canonical_json_rejects_nested_non_finite_numbers():
    with pytest.raises(ValueError, match="finite"):
        canonical_json_bytes({"items": [1.0, {"value": math.nan}]})


def test_canonical_json_rejects_nested_non_string_object_keys():
    with pytest.raises(TypeError, match="string keys"):
        canonical_json_bytes({"items": [{1: "value"}]})


def test_canonical_json_keeps_exact_integers_and_boolean_null_tokens():
    assert canonical_json_bytes({"n": 10**20, "yes": True, "none": None}) == (
        b'{"n":100000000000000000000,"none":null,"yes":true}\n'
    )


def test_canonical_json_rejects_polymorphic_integer_and_string_subclasses():
    class JsonBreakingInteger(int):
        def __str__(self) -> str:
            return "NaN"

    class FormattingString(str):
        def __format__(self, spec: str) -> str:
            return "allowed\nforged=value"

    with pytest.raises(TypeError):
        canonical_json_bytes(JsonBreakingInteger(1))
    with pytest.raises(TypeError):
        canonical_json_bytes(FormattingString("safe"))
    with pytest.raises(TypeError):
        id_lines("sfep-bundle-id/v1", {"safe": FormattingString("allowed")})


def test_numpy_real_scalars_are_normalized_and_quantiles_return_builtin_float():
    assert canonical_json_bytes(np.float64(1.5)) == b"1.5\n"
    assert type1_quantile([np.float64(1.0), np.float64(2.0)], np.float64(0.5)) == 1.0
    assert type(type1_quantile([np.float64(1.0)], np.float64(1.0))) is float

    with pytest.raises(TypeError):
        type1_quantile([True], 0.5)
    with pytest.raises(TypeError):
        type1_quantile([1.0], True)
    with pytest.raises(TypeError):
        type1_quantile([np.bool_(True)], 0.5)
    with pytest.raises(TypeError):
        type1_quantile([1.0], np.bool_(True))


def test_canonical_json_encodes_unique_finite_binary64_batch_within_ci_budget():
    values = [
        math.ldexp(1.0 + index / 997.0, -300 + ((index * 47) % 600))
        for index in range(128)
    ]

    started = time.perf_counter()
    encoded = [canonical_json_bytes(value) for value in values]
    elapsed = time.perf_counter() - started

    assert len(set(encoded)) == len(values)
    assert elapsed < 1.0, f"encoded 128 unique binary64 values in {elapsed:.3f}s"


def test_canonical_json_matches_independent_exhaustive_short_decimal_oracle():
    oracle: dict[float, str] = {}
    for coefficient in range(1, 1_000):
        if coefficient % 10 == 0:
            continue
        digits = str(coefficient)
        for exponent in range(-3, 10):
            point = len(digits) + exponent
            fixed = (
                digits + ("0" * exponent)
                if exponent >= 0
                else (
                    digits[:point] + "." + digits[point:]
                    if point > 0
                    else "0." + ("0" * (-point)) + digits
                )
            )
            spellings = [fixed]
            for decimal_point in range(1, len(digits) + 1):
                mantissa = (
                    digits
                    if decimal_point == len(digits)
                    else digits[:decimal_point] + "." + digits[decimal_point:]
                )
                spellings.append(f"{mantissa}e{exponent + len(digits) - decimal_point}")
            for spelling in spellings:
                for signed in (spelling, "-" + spelling):
                    if len(signed) > 3:
                        continue
                    parsed = float(signed)
                    incumbent = oracle.get(parsed)
                    if incumbent is None or (len(signed), signed) < (len(incumbent), incumbent):
                        oracle[parsed] = signed

    for value, expected in oracle.items():
        assert canonical_json_bytes(value) == (expected + "\n").encode("utf-8")


def test_canonical_json_matches_independent_integral_rounding_interval_oracle():
    values = []
    for binary_exponent in range(54, 61):
        ulp = 1 << (binary_exponent - 52)
        values.extend(float((1 << binary_exponent) + offset * ulp) for offset in range(8))

    for value in [*values, *[-item for item in values]]:
        magnitude = abs(value)
        target = Fraction.from_float(magnitude)
        lower = (target + Fraction.from_float(math.nextafter(magnitude, -math.inf))) / 2
        following = math.nextafter(magnitude, math.inf)
        upper = (target + Fraction.from_float(following)) / 2
        candidates = []
        maximum_length = len(str(int(magnitude))) + (1 if value < 0 else 0)
        for scale in range(maximum_length + 1):
            divisor = 10**scale
            first = (lower.numerator // (lower.denominator * divisor)) - 1
            last = (upper.numerator // (upper.denominator * divisor)) + 1
            for coefficient in range(max(1, first), last + 1):
                if coefficient % 10 == 0:
                    continue
                digits = str(coefficient)
                spellings = [digits + ("0" * scale)]
                for decimal_point in range(1, len(digits) + 1):
                    mantissa = (
                        digits
                        if decimal_point == len(digits)
                        else digits[:decimal_point] + "." + digits[decimal_point:]
                    )
                    spellings.append(f"{mantissa}e{scale + len(digits) - decimal_point}")
                for spelling in spellings:
                    signed = spelling if value > 0 else "-" + spelling
                    if len(signed) <= maximum_length and float(signed) == value:
                        candidates.append(signed)
        expected = min(candidates, key=lambda candidate: (len(candidate), candidate))

        assert canonical_json_bytes(value) == (expected + "\n").encode("utf-8")


def test_canonical_json_round_trips_random_and_extreme_finite_binary64_values():
    extremes = [
        float.fromhex("0x0.0000000000001p-1022"),
        float.fromhex("0x1.0000000000000p-1022"),
        float.fromhex("0x1.fffffffffffffp+1023"),
    ]
    generator = random.Random(20260826)
    random_values = []
    while len(random_values) < 256:
        value = struct.unpack(">d", generator.getrandbits(64).to_bytes(8, "big"))[0]
        if math.isfinite(value):
            random_values.append(value)

    for value in [*extremes, *[-item for item in extremes], *random_values]:
        spelling = canonical_json_bytes(value).decode("utf-8").strip()
        assert float(spelling) == value
        assert "e+" not in spelling
        assert not any(token in spelling for token in ("e-0", "e00", "e01"))


def test_canonical_json_and_id_lines_serialize_stateful_mappings_from_one_snapshot():
    class StatefulJsonMapping(Mapping):
        def __getitem__(self, key: object) -> object:
            return "not-the-snapshot"

        def __iter__(self):
            return iter((1,))

        def __len__(self) -> int:
            return 1

        def items(self):
            return iter((("b", "2"), ("a", "1")))

    class StatefulIdMapping(Mapping):
        def __getitem__(self, key: object) -> object:
            return "allowed\nforged=value"

        def __iter__(self):
            return iter(("forged=key",))

        def __len__(self) -> int:
            return 1

        def items(self):
            return iter((("b", "2"), ("a", "1")))

    assert canonical_json_bytes(StatefulJsonMapping()) == b'{"a":"1","b":"2"}\n'
    assert id_lines("sfep-bundle-id/v1", StatefulIdMapping()) == b"sfep-bundle-id/v1\na=1\nb=2\n"


def test_material_key_matches_independent_sha256_vector():
    assert digest_json_id(
        "sfep-material-key/v1", {"chargeId": "CH1", "slabNo": "1"}
    ) == "sha256:b313bfca80b92882d529a186bb7fc082e65020c409956481283745421ec1ce5a"


def test_id_lines_sorts_keys_by_utf8_and_terminates_each_line():
    assert id_lines("sfep-bundle-id/v1", {"한글": "값", "a": "1"}) == (
        "sfep-bundle-id/v1\na=1\n한글=값\n".encode("utf-8")
    )


@pytest.mark.parametrize(
    ("version", "fields"),
    [
        ("bad\nversion", {}),
        ("bad=version", {}),
        ("sfep-bundle-id/v1", {"bad=key": "value"}),
        ("sfep-bundle-id/v1", {"key": "bad\rvalue"}),
    ],
)
def test_id_lines_rejects_reserved_delimiters(version, fields):
    with pytest.raises(ValueError):
        id_lines(version, fields)


def test_sha256_uri_matches_empty_message_vector():
    assert sha256_uri(b"") == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_type1_quantile_uses_inverse_empirical_boundary():
    assert type1_quantile([1.0, 2.0, 3.0, 4.0], 0.25) == 1.0
    assert type1_quantile([1.0, 2.0, 3.0, 4.0], 0.50) == 2.0
    assert type1_quantile([1.0, 2.0, 3.0, 4.0], 1.00) == 4.0
    assert type1_quantile([], 0.50) is None


@pytest.mark.parametrize("values, q", [([1.0, math.nan], 0.5), ([1.0], 0.0), ([1.0], 1.1)])
def test_type1_quantile_rejects_non_finite_values_and_invalid_probability(values, q):
    with pytest.raises(ValueError):
        type1_quantile(values, q)
