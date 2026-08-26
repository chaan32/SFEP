from __future__ import annotations

import json
import math
from pathlib import Path

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
