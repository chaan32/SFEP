from __future__ import annotations

from dataclasses import replace
from datetime import date
import hashlib
import json
import stat

import pytest

import equipment_quality.artifacts as artifact_module
import equipment_quality.event_builder as event_builder_module
from equipment_quality.artifacts import (
    FIXED_ARTIFACT_FILENAMES,
    FIXED_ARTIFACT_ROLES,
    BundleLockError,
    NonDeterministicBundleError,
    PublishDurabilityError,
    compute_bundle_identity,
    compute_criteria_identity,
    output_lock,
    verify_complete_bundle,
    write_bundle,
)
from equipment_quality.criteria_projection import build_criteria_projection
from equipment_quality.models import AggregateLineage, Identity, SourceFile
from equipment_quality.schema import validate_normative_instance
from equipment_quality.summary import build_summary, holdout_metrics
from equipment_quality.deterministic import canonical_json_bytes, id_lines, sha256_uri
from factories.artifacts import (
    CONTRACT_ROOT,
    LITERAL_BUNDLE_ID,
    LITERAL_GOLDEN_ARTIFACT_SNAPSHOT,
    LITERAL_GOLDEN_BUNDLE_ID,
    LITERAL_CRITERIA_ID,
    LITERAL_CRITERIA_SCHEMA_DIGESTS,
    LITERAL_EIGHT_CRITERIA_FIELDS,
    LITERAL_NINETEEN_BUNDLE_FIELDS,
    StatefulMapping,
    bundle_request,
    definitions_with_stage_change,
    golden_split_and_definitions,
    golden_summary_request,
    expected_golden_summary_bytes,
    holdout_rule,
    literal_bundle_identity_inputs,
    literal_criteria_identity_inputs,
    mutate_holdout,
    mutate_mature_feature,
    mutate_mature_label,
)


def test_literal_criteria_identity_has_exact_eight_fields_and_id() -> None:
    identity = compute_criteria_identity(**literal_criteria_identity_inputs())
    assert dict(identity.fields) == LITERAL_EIGHT_CRITERIA_FIELDS
    assert identity.version == "sfep-criteria-id/v1"
    assert identity.value == LITERAL_CRITERIA_ID


def test_literal_bundle_identity_has_exact_nineteen_fields_and_id() -> None:
    identity = compute_bundle_identity(**literal_bundle_identity_inputs())
    assert dict(identity.fields) == LITERAL_NINETEEN_BUNDLE_FIELDS
    assert identity.version == "sfep-bundle-id/v1"
    assert identity.value == LITERAL_BUNDLE_ID


def test_identity_rejects_a_digest_that_does_not_authenticate_its_fields() -> None:
    with pytest.raises(ValueError, match="match"):
        Identity(
            value="sha256:" + "0" * 64,
            version="sfep-criteria-id/v1",
            fields=LITERAL_EIGHT_CRITERIA_FIELDS,
        )

    malformed = dict(LITERAL_EIGHT_CRITERIA_FIELDS)
    malformed["analysis_config_sha256"] = "not-a-sha"
    with pytest.raises(ValueError, match="sha256"):
        Identity(
            value=sha256_uri(id_lines("sfep-criteria-id/v1", malformed)),
            version="sfep-criteria-id/v1",
            fields=malformed,
        )


def test_range_aggregate_accepts_only_the_executed_stage_and_finite_filters() -> None:
    aggregate = AggregateLineage(
        artifact_role="equipment_operating_ranges",
        rule_id="sha256:" + "1" * 64,
        split="REFERENCE",
        population_ref="REFERENCE",
        input_material_keys=(),
        comparator_definition="NOT_APPLICABLE",
        filters=("STAGE_AVAILABLE_AT_AS_OF", "FINITE_VALUE"),
        transformations=("TYPE1_QUANTILE",),
    )
    assert aggregate.filters == ("STAGE_AVAILABLE_AT_AS_OF", "FINITE_VALUE")


def test_identity_snapshots_stateful_schema_mapping_once() -> None:
    inputs = literal_criteria_identity_inputs()
    inputs["schema_digests"] = StatefulMapping(
        LITERAL_CRITERIA_SCHEMA_DIGESTS,
        {**LITERAL_CRITERIA_SCHEMA_DIGESTS, "analysis_summary": "sha256:" + "f" * 64},
    )
    assert compute_criteria_identity(**inputs).value == LITERAL_CRITERIA_ID


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: {**value, "analysis_summary": "sha256:" + "f" * 64}, "schema roles"),
        (lambda value: {key: digest for key, digest in value.items() if key != "producer_runtime"}, "schema roles"),
        (lambda value: {**value, "producer_runtime": "sha256:ABC"}, "sha256"),
    ],
)
def test_criteria_identity_rejects_schema_role_or_digest_drift(mutation, message) -> None:
    inputs = literal_criteria_identity_inputs()
    inputs["schema_digests"] = mutation(dict(LITERAL_CRITERIA_SCHEMA_DIGESTS))
    with pytest.raises((TypeError, ValueError), match=message):
        compute_criteria_identity(**inputs)


def test_identity_rejects_bool_numeric_impostor_and_wrong_fixed_source_name() -> None:
    inputs = literal_bundle_identity_inputs()
    sources = list(inputs["sources"])
    sources[0] = SourceFile("sm_cc", "sts_1sm_cc_1.csv", True, "sha256:" + "5" * 64)
    inputs["sources"] = tuple(sources)
    with pytest.raises((TypeError, ValueError), match="size"):
        compute_bundle_identity(**inputs)

    inputs = literal_bundle_identity_inputs()
    sources = list(inputs["sources"])
    sources[0] = SourceFile("sm_cc", "renamed.csv", 11, "sha256:" + "5" * 64)
    inputs["sources"] = tuple(sources)
    with pytest.raises(ValueError, match="source name"):
        compute_bundle_identity(**inputs)


def test_identity_rejects_malformed_date_and_nonbuilt_in_bytes() -> None:
    inputs = literal_criteria_identity_inputs()
    inputs["as_of"] = "2025-02-20"
    with pytest.raises(TypeError, match="date"):
        compute_criteria_identity(**inputs)

    class BytesSubclass(bytes):
        pass

    inputs = literal_criteria_identity_inputs()
    inputs["criteria_projection"] = BytesSubclass(b"projection\n")
    with pytest.raises(TypeError, match="bytes"):
        compute_criteria_identity(**inputs)


def test_golden_projection_matches_frozen_bytes_and_hash() -> None:
    split, items = golden_split_and_definitions()
    actual = build_criteria_projection(split, items)
    expected = (CONTRACT_ROOT / "golden-expectation" / "criteria_projection.jsonl").read_bytes()
    assert actual == expected
    assert len(actual.splitlines()) == 268
    assert len(actual) == 79_246
    assert hashlib.sha256(actual).hexdigest() == (
        "6842fabd1cc79ca801e34bb0708498f4bea16317d4449e6d773e28e40c94ff5c"
    )


def test_projection_excludes_holdout_in_both_directions() -> None:
    split, items = golden_split_and_definitions()
    original = build_criteria_projection(split, items)
    mutated = mutate_holdout(split)
    assert build_criteria_projection(mutated, items) == original
    assert build_criteria_projection(split, items) == build_criteria_projection(mutated, items)


def test_projection_changes_for_mature_feature_and_label() -> None:
    split, items = golden_split_and_definitions()
    original = build_criteria_projection(split, items)
    assert build_criteria_projection(mutate_mature_feature(split), items) != original
    assert build_criteria_projection(mutate_mature_label(split), items) != original


def test_projection_uses_supplied_stage_definition_instead_of_private_map() -> None:
    split, items = golden_split_and_definitions()
    original = build_criteria_projection(split, items)
    changed = build_criteria_projection(split, definitions_with_stage_change(items))
    assert changed != original


def test_golden_summary_matches_complete_frozen_template() -> None:
    request = golden_summary_request()
    summary = build_summary(request)
    actual = canonical_json_bytes(summary)
    assert actual == expected_golden_summary_bytes(request)
    validate_normative_instance("analysis_summary.schema.json", summary)


def test_golden_summary_has_balanced_counts_and_complete_lineage() -> None:
    summary = build_summary(golden_summary_request())
    for count in summary["splitCounts"].values():
        assert count["total"] == (
            count["defects"] + count["nonDefects"] + count["unknownOrCensored"]
        )
    lineage = summary["lineage"]
    assert len(lineage["fields"]) == 345
    assert len(lineage["materials"]) == 12
    assert [item["populationRef"] for item in lineage["populations"]] == [
        "REFERENCE",
        "DISCOVERY",
        "CONFIRMATION",
        "HOLDOUT",
    ]
    assert len(lineage["aggregates"]) == 330
    role_counts = {}
    for field in lineage["fields"]:
        role_counts[field["artifactRole"]] = role_counts.get(field["artifactRole"], 0) + 1
    assert role_counts == {
        "bundle_manifest": 37,
        "analysis_config": 49,
        "producer_runtime": 35,
        "equipment_operating_ranges": 3,
        "quality_risk_intervals": 53,
        "replay_events": 61,
        "analysis_summary": 107,
    }


def test_summary_lineage_graph_is_closed_sorted_unique_and_acyclic() -> None:
    fields = build_summary(golden_summary_request())["lineage"]["fields"]
    identities = [f'{item["artifactRole"]}.{item["outputField"]}' for item in fields]
    role_rank = {
        role: index
        for index, role in enumerate(
            (
                "bundle_manifest",
                "analysis_config",
                "producer_runtime",
                "equipment_operating_ranges",
                "quality_risk_intervals",
                "replay_events",
                "analysis_summary",
            )
        )
    }
    assert fields == sorted(
        fields,
        key=lambda item: (
            role_rank[item["artifactRole"]],
            item["outputField"].encode("utf-8"),
        ),
    )
    assert len(identities) == len(set(identities))
    nodes = set(identities)
    graph = {
        node: [dependency for dependency in field["dependencies"] if dependency in nodes]
        for node, field in zip(identities, fields, strict=True)
    }
    for dependencies in graph.values():
        assert dependencies == sorted(set(dependencies), key=lambda value: value.encode("utf-8"))
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        assert node not in visiting
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def test_summary_material_ordinals_and_sidecar_aggregate_membership_are_exact() -> None:
    request = golden_summary_request()
    summary = build_summary(request)
    materials = summary["lineage"]["materials"]
    assert all(
        record["recordNumber"] >= 2
        for material in materials
        for record in material["sourceRecords"]
    )
    sidecars = {sidecar.rule_id: sidecar for sidecar in request.quality_rules.sidecars}
    populations = {
        item["populationRef"]: set(item["materialKeys"])
        for item in summary["lineage"]["populations"]
    }
    for aggregate in summary["lineage"]["aggregates"]:
        sidecar = sidecars[aggregate["ruleId"]]
        split_sidecar = (
            sidecar.discovery
            if aggregate["split"] == "DISCOVERY"
            else sidecar.confirmation
        )
        candidate = set(split_sidecar.candidate_material_keys)
        assert candidate.issubset(populations[aggregate["populationRef"]])
        assert sum(split_sidecar.comparator_counts_by_stratum.values()) <= len(
            populations[aggregate["populationRef"]] - candidate
        )
        expected_inputs = (
            sorted(candidate, key=lambda value: value.encode("utf-8"))
            if 0 < len(candidate) < len(populations[aggregate["populationRef"]])
            else []
        )
        assert aggregate["inputMaterialKeys"] == expected_inputs


def test_summary_request_deeply_snapshots_caller_owned_input_frames() -> None:
    request, caller_inputs = golden_summary_request(return_aliases=True)
    before = canonical_json_bytes(build_summary(request))
    caller_inputs.sm_cc.iloc[0, caller_inputs.sm_cc.columns.get_loc("tundish_temp")] = 9999.0
    assert canonical_json_bytes(build_summary(request)) == before


def test_summary_request_rejects_config_bytes_that_drift_from_identity() -> None:
    request = golden_summary_request()
    with pytest.raises(ValueError, match="analysis_config"):
        replace(request, analysis_config_bytes=b"{}\n")


def test_holdout_metrics_requires_pre_ap_eligible_grade_and_charge_bootstraps() -> None:
    split, _ = golden_split_and_definitions()
    profiles = holdout_metrics(split, [holdout_rule()], LITERAL_CRITERIA_ID)
    danger = profiles[0]
    assert danger["alertGrade"] == "DANGER"
    assert (danger["truePositive"], danger["falsePositive"]) == (1, 1)
    assert danger["precision"] == {
        "pointEstimate": 0.5,
        "lower": 0.5,
        "upper": 0.5,
        "validReplicates": 2000,
        "reasonCode": "NONE",
    }

    ignored = holdout_metrics(
        split,
        [holdout_rule(stage="AP_RECORDED_WITH_RESULT")],
        LITERAL_CRITERIA_ID,
    )[0]
    assert ignored["truePositive"] == 0
    assert ignored["falseNegative"] == 1
    assert ignored["precision"]["reasonCode"] == "ZERO_DENOMINATOR"


def _visible_bundles(output_root) -> list:
    return sorted(output_root.glob("sha256:*"), key=lambda path: path.name)


def _assert_no_partial_bundle(output_root) -> None:
    assert _visible_bundles(output_root) == []
    assert not any(
        path.name.startswith(".sfep-bundle-tmp-")
        for path in output_root.iterdir()
    )


def test_atomic_bundle_has_fixed_six_artifacts_and_manifest_without_paths(tmp_path) -> None:
    request = bundle_request(tmp_path)
    root = write_bundle(request)
    assert root == tmp_path / request.bundle_identity.value
    assert sorted(path.name for path in root.iterdir()) == sorted(
        [*FIXED_ARTIFACT_FILENAMES.values(), "bundle_manifest.json"]
    )
    manifest = json.loads((root / "bundle_manifest.json").read_bytes())
    assert [item["role"] for item in manifest["artifacts"]] == list(
        FIXED_ARTIFACT_ROLES
    )
    assert all("path" not in item for item in manifest["artifacts"])
    assert verify_complete_bundle(root, request)


def test_fixed_golden_bundle_snapshot_has_literal_sizes_and_hashes(tmp_path) -> None:
    request = bundle_request(tmp_path)
    root = write_bundle(request)
    assert root.name == LITERAL_GOLDEN_BUNDLE_ID
    actual = {
        path.name: (len(payload), hashlib.sha256(payload).hexdigest())
        for path in root.iterdir()
        for payload in (path.read_bytes(),)
    }
    assert actual == LITERAL_GOLDEN_ARTIFACT_SNAPSHOT


def test_bundle_request_deeply_snapshots_caller_owned_json_payloads(tmp_path) -> None:
    caller_summary = json.loads(expected_golden_summary_bytes(golden_summary_request()))
    request = bundle_request(tmp_path, analysis_summary=caller_summary)
    caller_summary["quarantineCounts"]["UNLINKED_AP"] = 999
    root = write_bundle(request)
    payload = (root / "analysis_summary.json").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        LITERAL_GOLDEN_ARTIFACT_SNAPSHOT["analysis_summary.json"][1]
    )


def test_bundle_schema_failure_precedes_temp_or_final_exposure(tmp_path) -> None:
    request = bundle_request(tmp_path, quality_schema_version="unknown/v9")
    with pytest.raises(Exception, match="sfep-quality-rules/v1"):
        write_bundle(request)
    _assert_no_partial_bundle(tmp_path)


def test_output_lock_rejects_concurrent_writer(tmp_path) -> None:
    request = bundle_request(tmp_path)
    with output_lock(tmp_path):
        with pytest.raises(BundleLockError, match="locked"):
            write_bundle(request)
    _assert_no_partial_bundle(tmp_path)


@pytest.mark.parametrize("phase", [*FIXED_ARTIFACT_ROLES, "bundle_manifest"])
def test_every_file_fsync_failure_cleans_temp_and_exposes_no_final(
    tmp_path, monkeypatch, phase
) -> None:
    request = bundle_request(tmp_path)
    original = artifact_module.fsync_file

    def fail_selected(descriptor: int, current_phase: str) -> None:
        if current_phase == phase:
            raise OSError("injected file fsync failure")
        original(descriptor, current_phase)

    monkeypatch.setattr(artifact_module, "fsync_file", fail_selected)
    with pytest.raises(OSError, match="injected file fsync failure"):
        write_bundle(request)
    _assert_no_partial_bundle(tmp_path)


def test_mid_artifact_write_failure_cleans_temp_and_exposes_no_final(
    tmp_path, monkeypatch
) -> None:
    request = bundle_request(tmp_path)
    original = artifact_module._write_artifact_file

    def fail_during_rules(path, payload, phase) -> None:
        if phase == "quality_risk_intervals":
            raise OSError("injected artifact write failure")
        original(path, payload, phase)

    monkeypatch.setattr(artifact_module, "_write_artifact_file", fail_during_rules)
    with pytest.raises(OSError, match="injected artifact write failure"):
        write_bundle(request)
    _assert_no_partial_bundle(tmp_path)


def test_silent_artifact_write_corruption_is_detected_before_exposure(
    tmp_path, monkeypatch
) -> None:
    request = bundle_request(tmp_path)
    original = artifact_module._write_artifact_file

    def corrupt_rules(path, payload, phase) -> None:
        original(
            path,
            b"{}\n" if phase == "quality_risk_intervals" else payload,
            phase,
        )

    monkeypatch.setattr(artifact_module, "_write_artifact_file", corrupt_rules)
    with pytest.raises(ValueError, match="written"):
        write_bundle(request)
    _assert_no_partial_bundle(tmp_path)


def test_temp_directory_fsync_failure_cleans_temp_and_exposes_no_final(
    tmp_path, monkeypatch
) -> None:
    request = bundle_request(tmp_path)
    original = artifact_module.fsync_directory

    def fail_temp(path) -> None:
        if path.name.startswith(".sfep-bundle-tmp-"):
            raise OSError("injected temp fsync failure")
        original(path)

    monkeypatch.setattr(artifact_module, "fsync_directory", fail_temp)
    with pytest.raises(OSError, match="injected temp fsync failure"):
        write_bundle(request)
    _assert_no_partial_bundle(tmp_path)


def test_atomic_rename_failure_cleans_temp_and_exposes_no_final(
    tmp_path, monkeypatch
) -> None:
    request = bundle_request(tmp_path)

    def fail_rename(source, target) -> None:
        raise OSError("injected rename failure")

    monkeypatch.setattr(artifact_module, "atomic_rename", fail_rename)
    with pytest.raises(OSError, match="injected rename failure"):
        write_bundle(request)
    _assert_no_partial_bundle(tmp_path)


def test_parent_fsync_failure_keeps_complete_visible_bundle_and_rerun_recovers(
    tmp_path, monkeypatch
) -> None:
    request = bundle_request(tmp_path)
    original = artifact_module.fsync_directory
    fail_once = True

    def fail_parent_once(path) -> None:
        nonlocal fail_once
        if path == tmp_path and fail_once:
            fail_once = False
            raise OSError("injected parent fsync failure")
        original(path)

    monkeypatch.setattr(artifact_module, "fsync_directory", fail_parent_once)
    with pytest.raises(
        PublishDurabilityError, match="PUBLISH_DURABILITY_UNKNOWN"
    ):
        write_bundle(request)
    visible = _visible_bundles(tmp_path)
    assert len(visible) == 1
    assert verify_complete_bundle(visible[0], request)

    monkeypatch.setattr(artifact_module, "fsync_directory", original)
    assert write_bundle(request) == visible[0]
    assert not any(
        path.name.startswith(".sfep-bundle-tmp-") for path in tmp_path.iterdir()
    )


def test_same_id_complete_bundle_is_reused_but_byte_drift_is_nondeterministic(
    tmp_path,
) -> None:
    request = bundle_request(tmp_path)
    root = write_bundle(request)
    assert write_bundle(request) == root

    (root / "analysis_summary.json").write_bytes(b"{}\n")
    assert not verify_complete_bundle(root, request)
    with pytest.raises(
        NonDeterministicBundleError, match="NON_DETERMINISTIC_BUNDLE"
    ):
        write_bundle(request)


def test_manifest_is_the_last_file_written(tmp_path, monkeypatch) -> None:
    request = bundle_request(tmp_path)
    original = artifact_module._write_artifact_file
    order: list[str] = []

    def record_order(path, payload, phase) -> None:
        order.append(path.name)
        original(path, payload, phase)

    monkeypatch.setattr(artifact_module, "_write_artifact_file", record_order)
    write_bundle(request)
    assert order == [
        *(FIXED_ARTIFACT_FILENAMES[role] for role in FIXED_ARTIFACT_ROLES),
        "bundle_manifest.json",
    ]


def test_replay_is_serialized_once_and_each_row_schema_validated_once(
    tmp_path, monkeypatch
) -> None:
    request = bundle_request(tmp_path)
    original_serialize = artifact_module.serialize_replay_events
    original_validate = event_builder_module.validate_normative_instance
    serialize_calls = 0
    replay_validations = 0

    def count_serialize(events):
        nonlocal serialize_calls
        serialize_calls += 1
        return original_serialize(events)

    def count_validate(schema_name, instance):
        nonlocal replay_validations
        if schema_name == "replay_event_row.schema.json":
            replay_validations += 1
        return original_validate(schema_name, instance)

    monkeypatch.setattr(artifact_module, "serialize_replay_events", count_serialize)
    monkeypatch.setattr(
        event_builder_module, "validate_normative_instance", count_validate
    )
    write_bundle(request)
    assert serialize_calls == 1
    assert replay_validations == len(request.replay_events)


def test_symlink_output_root_is_rejected(tmp_path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    request = bundle_request(alias)
    with pytest.raises(ValueError, match="symlink"):
        write_bundle(request)
    assert list(target.iterdir()) == []


def test_symlink_parent_cannot_escape_the_requested_output_path(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    request = bundle_request(alias / "output")
    with pytest.raises(ValueError, match="symlink"):
        write_bundle(request)
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("substitution", ["symlink", "directory", "wrong_mode", "extra"])
def test_complete_bundle_verifier_rejects_artifact_substitution(
    tmp_path, substitution
) -> None:
    request = bundle_request(tmp_path)
    root = write_bundle(request)
    artifact = root / "analysis_summary.json"
    if substitution == "symlink":
        payload = tmp_path / "outside.json"
        payload.write_bytes(artifact.read_bytes())
        artifact.unlink()
        artifact.symlink_to(payload)
    elif substitution == "directory":
        artifact.unlink()
        artifact.mkdir()
    elif substitution == "wrong_mode":
        artifact.chmod(0o644)
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o644
    else:
        (root / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    assert not verify_complete_bundle(root, request)
