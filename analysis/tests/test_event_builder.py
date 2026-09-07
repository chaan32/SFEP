"""Deterministic replay ID, stage visibility, ordering, and CSV contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
import csv
from collections import Counter
from collections.abc import Mapping
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest
from jsonschema import FormatChecker, ValidationError
from jsonschema.validators import validator_for

from equipment_quality import event_builder
from equipment_quality.event_builder import (
    ReplayEvent,
    batch_id,
    build_replay_events,
    equipment_batch_id,
    event_id,
    material_key,
    serialize_replay_events,
)
from equipment_quality.genealogy import build_genealogy
from equipment_quality.schema import read_inputs
from factories.events import (
    analysis_config,
    duplicated_material_chain,
    one_material_chain,
    replay_row_with_ap_columns_but_no_quality_row,
    two_distinct_materials,
    two_furnaces_same_hour,
    two_furnaces_same_hour_in_forward_input_order,
)


BUNDLE_ID = "sha256:" + "a" * 64
CRITERIA_ID = "sha256:" + "b" * 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXACT_19_COLUMN_HEADER = (
    "schema_version,bundle_id,criteria_id,event_id,replay_date,replay_hour,"
    "batch_kind,batch_id,equipment_batch_id,batch_step,time_precision,"
    "material_key,equipment_type,equipment_id,charge_id,slab_no,hr_coil_id,"
    "ap_prod_id,values_json"
)
LITERAL_STAGE_PAYLOAD_KEYS = {
    "CAST_RECORDED": {
        "sm_plant", "steel_grade", "steel_usage", "cc_gubun", "slab_gubun",
        "tundish_temp", "mlac_ratio", "delta_ferrite", "ingre_cr", "ingre_ni",
        "ingre_s", "slab_grind", "cast_date",
    },
    "FURNACE_CHARGED": {
        "furnace_no", "f_jangip_gubun", "f_jangip_temp", "slab_width",
    },
    "PREHEAT_COMPLETE": {"f_pre_temp", "f_pre_interval"},
    "HEAT_COMPLETE": {"f_heat_temp", "f_heat_interval"},
    "SOAK_COMPLETE": {"f_sock_temp", "f_sock_interval"},
    "FURNACE_EXTRACTED": {
        "f_bfg", "f_cog", "f_ldg", "f_bfg_ratio", "f_cog_ratio",
        "f_ldg_ratio", "f_ext_date", "f_ext_time",
    },
    "RM4_RECORDED": {"hr_date", "hr_thick", "hr_width", "rm4_temp", "rm_pitch"},
    "AP_RECORDED_WITH_RESULT": {
        "ap_plant", "ap_date", "ap_shift", "ap_thick", "ap_width",
        "ap_line_speed", "judge",
    },
}


def test_material_key_matches_contract_vector():
    assert material_key("CH1", "1") == (
        "sha256:b313bfca80b92882d529a186bb7fc082e65020c409956481283745421ec1ce5a"
    )


def test_batch_and_equipment_batch_ids_match_all_contract_vectors():
    assert batch_id("CAST_DAY", date(2025, 1, 1), None) == (
        "sha256:3cc8574421de7b7f3e7e1bb44f370ab280b114ff37ce8ce199b828da9a724821"
    )
    furnace_batch = batch_id("FURNACE_HOUR", date(2025, 1, 2), 7)
    assert furnace_batch == (
        "sha256:e10b5216ac881e81d4fd33c8fb119b32aa3914ad33c912e5e8f051f6e9e81827"
    )
    assert equipment_batch_id(furnace_batch, "FURNACE", "1") == (
        "sha256:fb1a1284c0e95473a1271bae72332f4b3f2064c9035839e6506af71399514ca4"
    )
    assert batch_id("AP_DAY", date(2025, 2, 1), None) == (
        "sha256:462c3bbd337ebcdee0a8e2aa2650deeabb08c329b089c764ec777929064fd537"
    )
    assert material_key("CH2", "1") != material_key("CH1", "1")


def test_cast_rm4_and_ap_event_ids_match_contract_and_golden_vectors():
    events = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    by_step = {event.batch_step: event for event in events}

    assert by_step["CAST_RECORDED"].event_id == (
        "sha256:d3b1e46114fbb696c042b30bb54a23540ede1df3c6357f7ad093097042642770"
    )
    assert by_step["RM4_RECORDED"].event_id == (
        "sha256:b704355b6ab1b10ab03715ea882c0e60499e39fa30dfa14ebf13872575c15766"
    )
    assert by_step["AP_RECORDED_WITH_RESULT"].event_id == (
        "sha256:7603ebac97ead3dc1881cb05a6638658ca0bb578284569d97fc3368173ec49d2"
    )
    assert event_id(by_step["CAST_RECORDED"]) == by_step["CAST_RECORDED"].event_id


def test_rm4_has_no_equipment_batch_and_uses_sequence_only_process_identity():
    rm4 = next(
        event
        for event in build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
        if event.batch_step == "RM4_RECORDED"
    )

    assert rm4.equipment_type == "RM4"
    assert rm4.equipment_id == "RM4_PROCESS"
    assert rm4.equipment_batch_id is None
    assert rm4.time_precision == "SEQUENCE_ONLY"


def test_same_hour_all_furnaces_advance_in_lockstep_with_raw_equipment_ids():
    events = build_replay_events(
        two_furnaces_same_hour(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    furnace_events = [event for event in events if event.batch_kind == "FURNACE_HOUR"]

    assert [event.batch_step for event in furnace_events] == [
        "FURNACE_CHARGED",
        "FURNACE_CHARGED",
        "PREHEAT_COMPLETE",
        "PREHEAT_COMPLETE",
        "HEAT_COMPLETE",
        "HEAT_COMPLETE",
        "SOAK_COMPLETE",
        "SOAK_COMPLETE",
        "FURNACE_EXTRACTED",
        "FURNACE_EXTRACTED",
        "RM4_RECORDED",
        "RM4_RECORDED",
    ]
    assert [event.equipment_id for event in furnace_events[:2]] == ["1", "2호기"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("charge_id", ""),
        ("slab_no", " 1"),
        ("cast_date", None),
        ("cast_date", datetime(2025, 1, 1)),
        ("f_ext_date", None),
        ("f_ext_time", None),
        ("f_ext_time", True),
        ("f_ext_time", 24),
        ("sm_plant", ""),
        ("furnace_no", ""),
        ("furnace_no", "5호기"),
        ("hr_coil_id", 1),
    ],
)
def test_required_replay_facts_fail_before_any_digest_is_computed(
    monkeypatch, field, value
):
    genealogy = one_material_chain(overrides={field: value})
    original_digest = event_builder.digest_json_id
    digest_calls = []

    def recording_digest(namespace, payload):
        digest_calls.append((namespace, payload))
        return original_digest(namespace, payload)

    monkeypatch.setattr(event_builder, "digest_json_id", recording_digest)

    with pytest.raises((TypeError, ValueError), match=field):
        build_replay_events(
            genealogy, BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
    assert digest_calls == []


@pytest.mark.parametrize("field", ["ap_date", "ap_plant", "ap_prod_id"])
def test_required_quality_ap_facts_fail_before_any_digest_is_computed(
    monkeypatch, field
):
    genealogy = one_material_chain()
    quality_rows = genealogy.quality_rows.copy(deep=True)
    quality_rows.at[quality_rows.index[0], field] = None
    invalid = replace(genealogy, quality_rows=quality_rows)
    original_digest = event_builder.digest_json_id
    digest_calls = []

    def recording_digest(namespace, payload):
        digest_calls.append((namespace, payload))
        return original_digest(namespace, payload)

    monkeypatch.setattr(event_builder, "digest_json_id", recording_digest)

    with pytest.raises((TypeError, ValueError), match=field):
        build_replay_events(invalid, BUNDLE_ID, CRITERIA_ID, analysis_config())
    assert digest_calls == []


@pytest.mark.parametrize("invalid_coil", [None, ""])
def test_quality_hr_coil_id_is_required_before_any_digest_is_computed(
    monkeypatch, invalid_coil
):
    genealogy = one_material_chain()
    quality_rows = genealogy.quality_rows.copy(deep=True)
    quality_rows.at[quality_rows.index[0], "hr_coil_id"] = invalid_coil
    invalid = replace(genealogy, quality_rows=quality_rows)
    original_digest = event_builder.digest_json_id
    digest_calls = []

    def recording_digest(namespace, payload):
        digest_calls.append((namespace, payload))
        return original_digest(namespace, payload)

    monkeypatch.setattr(event_builder, "digest_json_id", recording_digest)

    with pytest.raises((TypeError, ValueError), match="quality hr_coil_id"):
        build_replay_events(invalid, BUNDLE_ID, CRITERIA_ID, analysis_config())
    assert digest_calls == []


@pytest.mark.parametrize("foreign_coil", ["OTHER", "H002"])
def test_quality_hr_coil_id_must_match_its_replay_material_before_any_digest(
    monkeypatch, foreign_coil
):
    genealogy = (
        two_distinct_materials()
        if foreign_coil == "H002"
        else one_material_chain()
    )
    quality_rows = genealogy.quality_rows.copy(deep=True)
    first_material = quality_rows.index[0]
    quality_rows.at[first_material, "hr_coil_id"] = foreign_coil
    invalid = replace(genealogy, quality_rows=quality_rows)
    original_digest = event_builder.digest_json_id
    digest_calls = []

    def recording_digest(namespace, payload):
        digest_calls.append((namespace, payload))
        return original_digest(namespace, payload)

    monkeypatch.setattr(event_builder, "digest_json_id", recording_digest)

    with pytest.raises(ValueError, match="quality hr_coil_id must match replay"):
        build_replay_events(invalid, BUNDLE_ID, CRITERIA_ID, analysis_config())
    assert digest_calls == []


def test_builder_uses_preflight_snapshots_if_source_frames_mutate_during_hashing(
    monkeypatch,
):
    genealogy = one_material_chain()
    expected = build_replay_events(
        genealogy, BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    original_digest = event_builder.digest_json_id
    mutated = False

    def mutating_digest(namespace, payload):
        nonlocal mutated
        if not mutated:
            mutated = True
            genealogy.replay_rows.loc[:, "cast_date"] = None
            genealogy.replay_rows.loc[:, "f_ext_time"] = 22
            genealogy.quality_rows.loc[:, "ap_date"] = None
        return original_digest(namespace, payload)

    monkeypatch.setattr(event_builder, "digest_json_id", mutating_digest)

    assert build_replay_events(
        genealogy, BUNDLE_ID, CRITERIA_ID, analysis_config()
    ) == expected


def test_stage_payloads_are_exact_newly_visible_config_fields_without_raw_ratios():
    events = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    by_step = {event.batch_step: event for event in events}
    assert {
        step: set(event.values_json) for step, event in by_step.items()
    } == LITERAL_STAGE_PAYLOAD_KEYS
    assert all("judge" not in event.values_json for event in events[:-1])
    assert {"f_bfg_per", "f_cog_per", "f_ldg_per"}.isdisjoint(
        by_step["FURNACE_EXTRACTED"].values_json
    )


def test_future_identifiers_are_top_level_only_when_available():
    events = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    by_step = {event.batch_step: event for event in events}

    assert by_step["CAST_RECORDED"].slab_no == "1"
    assert by_step["CAST_RECORDED"].hr_coil_id is None
    assert by_step["FURNACE_EXTRACTED"].hr_coil_id is None
    assert by_step["RM4_RECORDED"].hr_coil_id == "H001"
    assert by_step["RM4_RECORDED"].ap_prod_id is None
    assert by_step["AP_RECORDED_WITH_RESULT"].ap_prod_id == "A001"
    assert all(
        {"charge_id", "slab_no", "hr_coil_id", "ap_prod_id"}.isdisjoint(
            event.values_json
        )
        for event in events
    )


def test_missing_newly_visible_value_keeps_key_with_json_null():
    preheat = next(
        event
        for event in build_replay_events(
            one_material_chain(missing="f_pre_temp"),
            BUNDLE_ID,
            CRITERIA_ID,
            analysis_config(),
        )
        if event.batch_step == "PREHEAT_COMPLETE"
    )

    assert "f_pre_temp" in preheat.values_json
    assert preheat.values_json["f_pre_temp"] is None


def test_duplicate_event_is_fatal_even_when_semantic_preimage_is_identical():
    with pytest.raises(ValueError, match="duplicate event"):
        build_replay_events(
            duplicated_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )


def test_cross_namespace_digest_preimage_collision_is_fatal(monkeypatch):
    monkeypatch.setattr(
        event_builder, "digest_json_id", lambda *_: "sha256:" + "0" * 64
    )

    with pytest.raises(ValueError, match="collision"):
        build_replay_events(
            two_distinct_materials(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )


@pytest.mark.parametrize(
    "backend_result",
    [123, [], {}, "sha256:" + "A" * 64, "sha256:1234"],
)
@pytest.mark.parametrize(
    "api_name",
    ["material_key", "batch_id", "equipment_batch_id", "event_id", "builder"],
)
def test_every_digest_backend_result_is_an_exact_lowercase_sha256_uri(
    monkeypatch, backend_result, api_name
):
    cast = next(
        event
        for event in build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
        if event.batch_step == "CAST_RECORDED"
    )
    furnace_batch = batch_id("FURNACE_HOUR", date(2025, 1, 2), 7)
    monkeypatch.setattr(
        event_builder, "digest_json_id", lambda *_: backend_result
    )

    calls = {
        "material_key": lambda: material_key("CH1", "1"),
        "batch_id": lambda: batch_id("CAST_DAY", date(2025, 1, 1), None),
        "equipment_batch_id": lambda: equipment_batch_id(
            furnace_batch, "FURNACE", "1"
        ),
        "event_id": lambda: event_id(cast),
        "builder": lambda: build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        ),
    }

    with pytest.raises((TypeError, ValueError), match="digest_json_id result"):
        calls[api_name]()


def test_replay_event_is_frozen_and_defensively_snapshots_values_mapping():
    original = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )[0]
    caller_values = {"sm_plant": "before"}
    copied = replace(original, values_json=caller_values)
    caller_values["sm_plant"] = "after"

    assert copied.values_json == {"sm_plant": "before"}
    with pytest.raises(TypeError):
        copied.values_json["sm_plant"] = "mutated"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        copied.batch_id = "sha256:" + "0" * 64  # type: ignore[misc]


def test_replay_event_reads_a_stateful_mapping_once_and_blocks_later_aliases():
    class StatefulMapping(Mapping):
        def __init__(self):
            self.calls = 0
            self.current = "first"

        def __getitem__(self, key):
            if key != "field":
                raise KeyError(key)
            return self.current

        def __iter__(self):
            return iter(("field",))

        def __len__(self):
            return 1

        def items(self):
            self.calls += 1
            return (("field", self.current),)

    original = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )[0]
    source = StatefulMapping()
    copied = replace(original, values_json=source)
    source.current = "second"

    assert source.calls == 1
    assert copied.values_json == {"field": "first"}


def test_replay_event_hash_matches_equality_and_canonical_payload_bytes():
    cast = next(
        event
        for event in build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
        if event.batch_step == "CAST_RECORDED"
    )
    reordered_values = dict(reversed(tuple(cast.values_json.items())))
    wire_equivalent = replace(cast, values_json=reordered_values)
    equal_copy = replace(cast)

    assert cast == equal_copy == wire_equivalent
    assert hash(cast) == hash(equal_copy) == hash(wire_equivalent)
    assert len({cast, equal_copy, wire_equivalent}) == 1


def test_replay_event_hash_is_stable_after_caller_mapping_mutation():
    cast = next(
        event
        for event in build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
        if event.batch_step == "CAST_RECORDED"
    )
    caller_values = dict(cast.values_json)
    copied = replace(cast, values_json=caller_values)
    before = hash(copied)
    caller_values["sm_plant"] = "MUTATED"

    assert copied == cast
    assert hash(copied) == before == hash(cast)


def test_replay_event_equality_and_hash_use_the_same_canonical_number_encoding():
    cast = next(
        event
        for event in build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
        if event.batch_step == "CAST_RECORDED"
    )
    integral_values = dict(cast.values_json, tundish_temp=1540)
    float_values = dict(cast.values_json, tundish_temp=1540.0)
    canonical_equal_int = replace(cast, values_json=integral_values)
    canonical_equal_float = replace(cast, values_json=float_values)
    large_int = replace(
        cast, values_json=dict(cast.values_json, tundish_temp=10**20)
    )
    large_float = replace(
        cast, values_json=dict(cast.values_json, tundish_temp=1e20)
    )

    assert canonical_equal_int == canonical_equal_float
    assert hash(canonical_equal_int) == hash(canonical_equal_float)
    assert len({canonical_equal_int, canonical_equal_float}) == 1
    assert {canonical_equal_int: "int", canonical_equal_float: "float"} == {
        canonical_equal_int: "float"
    }
    assert large_int != large_float
    assert hash(large_int) != hash(large_float)
    assert len({large_int, large_float}) == 2
    assert len({large_int: "int", large_float: "float"}) == 2


def test_replay_event_canonical_equality_is_order_independent_and_symmetric():
    cast = next(
        event
        for event in build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
        if event.batch_step == "CAST_RECORDED"
    )
    reordered = replace(
        cast, values_json=dict(reversed(tuple(cast.values_json.items())))
    )
    foreign = object()

    assert cast == reordered
    assert reordered == cast
    assert hash(cast) == hash(reordered)
    assert cast.__eq__(foreign) is NotImplemented
    assert (cast == foreign) is False
    assert (foreign == cast) is False


@pytest.mark.parametrize(
    ("values", "error", "message"),
    [
        ({"nested": []}, TypeError, "scalar"),
        ({"flag": True}, TypeError, "boolean"),
        ({"number": math.inf}, ValueError, "finite"),
        ({1: "value"}, TypeError, "key"),
    ],
)
def test_replay_event_rejects_non_wire_values(values, error, message):
    original = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )[0]

    with pytest.raises(error, match=message):
        replace(original, values_json=values)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema_version": "sfep-replay-events/v2"}, "schema_version"),
        ({"bundle_id": "sha256:BAD"}, "bundle_id"),
        ({"replay_date": datetime(2025, 1, 1)}, "replay_date"),
        ({"replay_hour": True}, "replay_hour"),
        ({"batch_kind": "FUTURE_DAY"}, "batch_kind"),
        ({"batch_step": "UNKNOWN"}, "batch_step"),
        ({"time_precision": "MINUTE"}, "time_precision"),
        ({"equipment_type": "UNKNOWN"}, "equipment_type"),
        ({"equipment_id": ""}, "equipment_id"),
        ({"hr_coil_id": "TOO_EARLY"}, "CAST_RECORDED"),
    ],
)
def test_replay_event_rejects_invalid_builtin_hash_enum_and_stage_matrix(
    changes, message
):
    cast = next(
        event
        for event in build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
        if event.batch_step == "CAST_RECORDED"
    )

    with pytest.raises((TypeError, ValueError), match=message):
        replace(cast, **changes)


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: material_key(True, "1"), "charge_id"),
        (lambda: batch_id("CAST_DAY", date(2025, 1, 1), 0), "day batches"),
        (lambda: batch_id("FURNACE_HOUR", date(2025, 1, 1), True), "hour"),
        (lambda: batch_id("CAST_DAY", datetime(2025, 1, 1), None), "date"),
        (lambda: equipment_batch_id("sha256:BAD", "FURNACE", "1"), "batch_id"),
        (lambda: event_id(object()), "ReplayEvent"),
    ],
)
def test_public_id_builders_reject_implicit_coercion(call, message):
    with pytest.raises((TypeError, ValueError), match=message):
        call()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"f_pre_temp": True}, "boolean"),
        ({"f_pre_temp": np.bool_(True)}, "boolean"),
        ({"f_pre_temp": math.inf}, "finite"),
    ],
)
def test_builder_rejects_bool_as_number_and_genuine_nonfinite_values(
    overrides, message
):
    with pytest.raises((TypeError, ValueError), match=message):
        build_replay_events(
            one_material_chain(overrides=overrides),
            BUNDLE_ID,
            CRITERIA_ID,
            analysis_config(),
        )


def test_pandas_nan_source_missingness_becomes_json_null():
    events = build_replay_events(
        one_material_chain(overrides={"f_pre_temp": math.nan}),
        BUNDLE_ID,
        CRITERIA_ID,
        analysis_config(),
    )
    preheat = next(event for event in events if event.batch_step == "PREHEAT_COMPLETE")

    assert preheat.values_json["f_pre_temp"] is None


def test_csv_uses_exact_header_lf_empty_nulls_and_canonical_values_json():
    payload = serialize_replay_events(
        build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
    )
    rows = list(
        csv.DictReader(io.StringIO(payload.decode("utf-8"), newline=""), strict=True)
    )
    rm4 = next(row for row in rows if row["batch_step"] == "RM4_RECORDED")

    assert payload.startswith(EXACT_19_COLUMN_HEADER.encode("ascii") + b"\n")
    assert not payload.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in payload
    assert payload.endswith(b"\n")
    assert rm4["equipment_batch_id"] == ""
    assert rm4["ap_prod_id"] == ""
    assert rm4["replay_hour"] == "7"
    assert rm4["values_json"] == (
        '{"hr_date":"2025-01-01","hr_thick":3,"hr_width":1210,'
        '"rm4_temp":1040,"rm_pitch":2.1}'
    )


def test_serialized_rows_validate_against_normative_replay_schema():
    schema = json.loads(
        (
            REPOSITORY_ROOT
            / "contracts/equipment-monitor/v1/replay_event_row.schema.json"
        ).read_text(encoding="utf-8")
    )
    validator = validator_for(schema)(schema, format_checker=FormatChecker())
    payload = serialize_replay_events(
        build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
    )
    rows = csv.DictReader(io.StringIO(payload.decode("utf-8"), newline=""), strict=True)

    for row in rows:
        instance = dict(row)
        instance["replay_hour"] = (
            int(instance["replay_hour"]) if instance["replay_hour"] else None
        )
        instance["equipment_batch_id"] = instance["equipment_batch_id"] or None
        instance["hr_coil_id"] = instance["hr_coil_id"] or None
        instance["ap_prod_id"] = instance["ap_prod_id"] or None
        instance["values_json"] = json.loads(instance["values_json"])
        validator.validate(instance)


def test_serializer_rejects_missing_and_future_stage_payload_keys():
    cast = next(
        event
        for event in build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
        if event.batch_step == "CAST_RECORDED"
    )
    missing = dict(cast.values_json)
    missing.pop("cast_date")
    future = dict(cast.values_json)
    future["f_pre_temp"] = 1080.0

    for invalid in (
        replace(cast, values_json={"judge": "불량"}),
        replace(cast, values_json=missing),
        replace(cast, values_json=future),
    ):
        with pytest.raises(ValidationError):
            serialize_replay_events([invalid])


def test_serializer_rejects_wrong_payload_scalar_date_and_hour_types():
    events = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    cast = next(event for event in events if event.batch_step == "CAST_RECORDED")
    extracted = next(
        event for event in events if event.batch_step == "FURNACE_EXTRACTED"
    )
    wrong_number = dict(cast.values_json)
    wrong_number["tundish_temp"] = "1540"
    impossible_date = dict(cast.values_json)
    impossible_date["cast_date"] = "2025-02-30"
    wrong_hour = dict(extracted.values_json)
    wrong_hour["f_ext_time"] = 24

    for invalid in (
        replace(cast, values_json=wrong_number),
        replace(cast, values_json=impossible_date),
        replace(extracted, values_json=wrong_hour),
    ):
        with pytest.raises(ValidationError):
            serialize_replay_events([invalid])


def test_serializer_rejects_object_setattr_forged_row_before_writing_bytes():
    events = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    cast = next(event for event in events if event.batch_step == "CAST_RECORDED")
    rm4 = next(event for event in events if event.batch_step == "RM4_RECORDED")

    forged_cases = (
        (cast, "bundle_id", "SHA256:" + "a" * 64),
        (cast, "replay_date", "2025-01-01"),
        (cast, "hr_coil_id", "TOO_EARLY"),
        (rm4, "hr_coil_id", None),
    )
    for original, field, value in forged_cases:
        forged = replace(original)
        object.__setattr__(forged, field, value)
        with pytest.raises((ValidationError, TypeError, ValueError)):
            serialize_replay_events([forged])


def test_serializer_snapshots_a_forged_stateful_values_mapping_exactly_once():
    class StatefulMapping(Mapping):
        def __init__(self, values):
            self.values = values
            self.calls = 0

        def __getitem__(self, key):
            return self.values[key]

        def __iter__(self):
            return iter(self.values)

        def __len__(self):
            return len(self.values)

        def items(self):
            self.calls += 1
            return tuple(self.values.items())

    cast = next(
        event
        for event in build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
        )
        if event.batch_step == "CAST_RECORDED"
    )
    source = StatefulMapping(dict(cast.values_json))
    forged = replace(cast)
    object.__setattr__(forged, "values_json", source)

    payload = serialize_replay_events([forged])

    assert source.calls == 1
    assert b'""sm_plant"":""SM1""' in payload


def test_csv_round_trips_unicode_commas_quotes_and_newlines_deterministically():
    genealogy = one_material_chain(
        overrides={"sm_plant": '한글,\n"인용"'}
    )
    first = serialize_replay_events(
        build_replay_events(genealogy, BUNDLE_ID, CRITERIA_ID, analysis_config())
    )
    second = serialize_replay_events(
        build_replay_events(genealogy, BUNDLE_ID, CRITERIA_ID, analysis_config())
    )
    rows = csv.DictReader(io.StringIO(first.decode("utf-8"), newline=""), strict=True)
    cast = next(row for row in rows if row["batch_step"] == "CAST_RECORDED")

    assert first == second
    assert json.loads(cast["values_json"])["sm_plant"] == '한글,\n"인용"'


def test_build_and_csv_are_independent_of_source_row_order():
    reverse_input = build_replay_events(
        two_furnaces_same_hour(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    forward_input = build_replay_events(
        two_furnaces_same_hour_in_forward_input_order(),
        BUNDLE_ID,
        CRITERIA_ID,
        analysis_config(),
    )

    assert reverse_input == forward_input
    assert serialize_replay_events(reverse_input) == serialize_replay_events(forward_input)


def test_serializer_rejects_non_monotonic_and_duplicate_sort_tuples():
    events = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )

    with pytest.raises(ValueError, match="strictly monotonic"):
        serialize_replay_events(list(reversed(events)))
    with pytest.raises(ValueError, match="duplicate event"):
        serialize_replay_events([events[0], events[0]])


@pytest.mark.parametrize("field", ["bundle_id", "criteria_id"])
def test_serializer_rejects_mixed_bundle_or_criteria_identity(field):
    events = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    replacement = "sha256:" + "c" * 64
    events[1] = replace(events[1], **{field: replacement})

    with pytest.raises(ValueError, match=field):
        serialize_replay_events(events)


def test_furnace_equipment_sort_rejects_unknown_unit_without_rewriting_raw_id():
    with pytest.raises(ValueError, match="furnace_no"):
        build_replay_events(
            one_material_chain(overrides={"furnace_no": "5호기"}),
            BUNDLE_ID,
            CRITERIA_ID,
            analysis_config(),
        )


def test_ap_event_eligibility_comes_from_quality_rows_not_incidental_ap_columns():
    genealogy = replay_row_with_ap_columns_but_no_quality_row()

    events = build_replay_events(
        genealogy, BUNDLE_ID, CRITERIA_ID, analysis_config()
    )

    assert len(events) == 7
    assert all(event.batch_step != "AP_RECORDED_WITH_RESULT" for event in events)
    assert len(events) == 7 * len(genealogy.replay_rows) + len(genealogy.quality_rows)


def test_config_cannot_move_judge_before_ap_result():
    config = analysis_config()
    fields = []
    for configured in config.fields:
        item = dict(configured)
        if item["field"] == "judge":
            item["firstAvailableStage"] = "CAST_RECORDED"
        fields.append(item)
    unsafe = replace(config, fields=tuple(fields))

    with pytest.raises(ValueError, match="judge.*AP_RECORDED_WITH_RESULT"):
        build_replay_events(
            one_material_chain(), BUNDLE_ID, CRITERIA_ID, unsafe
        )


def test_installed_wheel_serializer_uses_packaged_schema_from_arbitrary_cwd(
    tmp_path,
):
    wheel_source = tmp_path / "wheel-source"
    wheel_source.mkdir()
    shutil.copy2(
        REPOSITORY_ROOT / "analysis/pyproject.toml",
        wheel_source / "pyproject.toml",
    )
    shutil.copytree(
        REPOSITORY_ROOT / "analysis/equipment_quality",
        wheel_source / "equipment_quality",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    wheel_dir = tmp_path / "wheel"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(wheel_dir),
            str(wheel_source),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1

    installed_root = tmp_path / "installed"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(installed_root),
            str(wheels[0]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert install.returncode == 0, install.stdout + install.stderr

    outside_checkout = tmp_path / "outside-checkout"
    outside_checkout.mkdir()
    assert not (outside_checkout / "contracts").exists()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(installed_root)
    environment["PYTHONNOUSERSITE"] = "1"
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from dataclasses import replace
from datetime import date
from jsonschema import ValidationError
from equipment_quality.event_builder import ReplayEvent, serialize_replay_events

event = ReplayEvent(
    schema_version="sfep-replay-events/v1",
    bundle_id="sha256:" + "a" * 64,
    criteria_id="sha256:" + "b" * 64,
    event_id="sha256:d3b1e46114fbb696c042b30bb54a23540ede1df3c6357f7ad093097042642770",
    replay_date=date(2025, 1, 1),
    replay_hour=None,
    batch_kind="CAST_DAY",
    batch_id="sha256:3cc8574421de7b7f3e7e1bb44f370ab280b114ff37ce8ce199b828da9a724821",
    equipment_batch_id=None,
    batch_step="CAST_RECORDED",
    time_precision="DAY",
    material_key="sha256:b313bfca80b92882d529a186bb7fc082e65020c409956481283745421ec1ce5a",
    equipment_type="SM_CC",
    equipment_id="SM1",
    charge_id="CH1",
    slab_no="1",
    hr_coil_id=None,
    ap_prod_id=None,
    values_json={
        "sm_plant":"SM1","steel_grade":"STS304","steel_usage":"A",
        "cc_gubun":"CC1","slab_gubun":"NORMAL","tundish_temp":1540.0,
        "mlac_ratio":0.92,"delta_ferrite":7.1,"ingre_cr":18.2,
        "ingre_ni":8.1,"ingre_s":0.005,"slab_grind":"HSHS",
        "cast_date":"2025-01-01",
    },
)
assert serialize_replay_events([event]).startswith(b"schema_version,")
try:
    serialize_replay_events([replace(event, values_json={"judge":"불량"})])
except ValidationError:
    print("installed-serializer-schema-ok")
else:
    raise AssertionError("installed serializer accepted a future-leaking row")
""",
        ],
        cwd=outside_checkout,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.strip() == "installed-serializer-schema-ok"


def test_non_result_field_stage_is_driven_only_by_passed_config():
    config = analysis_config()
    fields = []
    for configured in config.fields:
        item = dict(configured)
        if item["field"] == "f_pre_temp":
            item["firstAvailableStage"] = "HEAT_COMPLETE"
        fields.append(item)
    moved = replace(config, fields=tuple(fields))

    moved_events = build_replay_events(
        one_material_chain(), BUNDLE_ID, CRITERIA_ID, moved
    )
    by_step = {
        event.batch_step: event
        for event in moved_events
    }

    assert set(by_step["PREHEAT_COMPLETE"].values_json) == {"f_pre_interval"}
    assert set(by_step["HEAT_COMPLETE"].values_json) == {
        "f_pre_temp", "f_heat_temp", "f_heat_interval"
    }
    with pytest.raises(ValidationError):
        serialize_replay_events(moved_events)


def test_full_golden_source_stream_has_95_events_and_stable_sha256():
    inputs = read_inputs(
        REPOSITORY_ROOT / "contracts/equipment-monitor/v1/golden-source"
    )
    genealogy = build_genealogy(inputs)
    events = build_replay_events(
        genealogy, BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    payload = serialize_replay_events(events)

    assert len(genealogy.replay_rows) == 12
    assert len(genealogy.quality_rows) == 11
    assert len(events) == 95
    assert len(events) == 7 * 12 + 11
    assert Counter(event.batch_step for event in events) == {
        "CAST_RECORDED": 12,
        "FURNACE_CHARGED": 12,
        "PREHEAT_COMPLETE": 12,
        "HEAT_COMPLETE": 12,
        "SOAK_COMPLETE": 12,
        "FURNACE_EXTRACTED": 12,
        "RM4_RECORDED": 12,
        "AP_RECORDED_WITH_RESULT": 11,
    }
    assert hashlib.sha256(payload).hexdigest() == (
        "4c1b3e599d55d5b99d4eea72c9b68ec7d506db2707b55d945700773cc821fe62"
    )


@pytest.mark.real_data
def test_actual_snapshot_builds_one_unique_schema_safe_deterministic_stream():
    data_dir = Path("/Users/haechan/프로젝트/sfep-steel/steel-data")
    if not data_dir.is_dir():
        pytest.skip("approved local steel snapshot is unavailable")
    inputs = read_inputs(data_dir)
    genealogy = build_genealogy(inputs)

    events = build_replay_events(
        genealogy, BUNDLE_ID, CRITERIA_ID, analysis_config()
    )
    payload = serialize_replay_events(events)

    assert len(genealogy.replay_rows) == 23631
    assert len(genealogy.quality_rows) == 23626
    assert len(events) == 189043
    assert len(events) == 7 * len(genealogy.replay_rows) + len(genealogy.quality_rows)
    assert len({event.event_id for event in events}) == len(events)
    assert len({event.material_key for event in events}) == len(genealogy.replay_rows)
    assert len({event.batch_id for event in events}) == 1268
    assert len(
        {
            event.equipment_batch_id
            for event in events
            if event.equipment_batch_id is not None
        }
    ) == 3831
    assert Counter(event.batch_step for event in events) == {
        "CAST_RECORDED": 23631,
        "FURNACE_CHARGED": 23631,
        "PREHEAT_COMPLETE": 23631,
        "HEAT_COMPLETE": 23631,
        "SOAK_COMPLETE": 23631,
        "FURNACE_EXTRACTED": 23631,
        "RM4_RECORDED": 23631,
        "AP_RECORDED_WITH_RESULT": 23626,
    }
    assert Counter(event.time_precision for event in events) == {
        "DAY": 47257,
        "HOUR_BUCKET": 118155,
        "SEQUENCE_ONLY": 23631,
    }
    assert min(event.replay_date for event in events) == date(2025, 2, 16)
    assert max(event.replay_date for event in events) == date(2025, 9, 30)
    assert {
        event.replay_hour
        for event in events
        if event.batch_kind == "FURNACE_HOUR"
    } == set(range(24))
    assert all(
        set(event.values_json) == LITERAL_STAGE_PAYLOAD_KEYS[event.batch_step]
        for event in events
    )
    assert all(
        "judge" not in event.values_json
        for event in events
        if event.batch_step != "AP_RECORDED_WITH_RESULT"
    )
    assert payload.startswith(EXACT_19_COLUMN_HEADER.encode("ascii") + b"\n")
    assert b"\r" not in payload
    assert hashlib.sha256(payload).hexdigest() == (
        "980bd764a0fce16405646985f24c9d43022cf8c40eed91233d38781300c2c56f"
    )
