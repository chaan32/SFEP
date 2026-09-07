from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

import equipment_quality.artifacts as artifact_module
from equipment_quality.bundle_contract import (
    V1_CONTRACT,
    V2_CONTRACT,
    contract_for_config_version,
    contract_for_manifest_version,
)
from equipment_quality.schema import (
    contract_schema_bytes,
    normative_schema_bytes,
    validate_contract_instance,
)
from factories.artifacts import LITERAL_GOLDEN_ARTIFACT_SNAPSHOT, bundle_request


ROLES = {
    "analysis_config",
    "analysis_summary",
    "bundle_manifest",
    "equipment_operating_ranges",
    "producer_runtime",
    "quality_risk_intervals",
    "replay_events",
}
V1_SCHEMA_RESOURCES = {
    "analysis_config": (
        "contracts/v1/analysis_config.schema.json",
        "fad28561dfe9d9fe3cd09b025bb18c2101be053cb094b08442ea45963b86f549",
    ),
    "analysis_summary": (
        "contracts/v1/analysis_summary.schema.json",
        "6cb6077cc7093b90e464cc794604ca6b7682002a138ebed051801aee29f9d374",
    ),
    "bundle_manifest": (
        "contracts/v1/bundle_manifest.schema.json",
        "666e880d296c0d7e3df5af1aa80e6865922ebfb337fb9aca48f93eddfd89e8a5",
    ),
    "equipment_operating_ranges": (
        "contracts/v1/equipment_operating_ranges.schema.json",
        "bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89",
    ),
    "producer_runtime": (
        "contracts/v1/producer_runtime.schema.json",
        "97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6",
    ),
    "quality_risk_intervals": (
        "contracts/v1/quality_risk_intervals.schema.json",
        "2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad",
    ),
    "replay_events": (
        "contracts/v1/replay_event_row.schema.json",
        "309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6",
    ),
}
V2_SCHEMA_RESOURCES = {
    **V1_SCHEMA_RESOURCES,
    "analysis_config": (
        "contracts/v2/analysis_config.schema.json",
        "7942b12abcfeee661290f5b40df922edfeec66d6f95dc1ce20e78cc0f58f5da1",
    ),
    "analysis_summary": (
        "contracts/v2/analysis_summary.schema.json",
        "0b38234ea0b7c5614dff0ac7514b6fb82e401a7846621e45bcbcf00ed82dc7c4",
    ),
    "bundle_manifest": (
        "contracts/v2/bundle_manifest.schema.json",
        "0d7c4fb5e939beb324c6d83005a83cb9702e480a5babdfede88e5ca05776c2c3",
    ),
}
V1_ARTIFACT_VERSIONS = {
    "analysis_config": "sfep-analysis-config/v1",
    "analysis_summary": "sfep-analysis-summary/v1",
    "equipment_operating_ranges": "sfep-operating-ranges/v1",
    "producer_runtime": "sfep-producer-runtime/v1",
    "quality_risk_intervals": "sfep-quality-rules/v1",
    "replay_events": "sfep-replay-events/v1",
}
V2_ARTIFACT_VERSIONS = {
    **V1_ARTIFACT_VERSIONS,
    "analysis_config": "sfep-analysis-config/v2",
    "analysis_summary": "sfep-analysis-summary/v2",
}


@pytest.mark.parametrize(
    ("contract", "manifest", "config", "summary", "versions", "resources"),
    [
        (
            V1_CONTRACT,
            "sfep-equipment-bundle/v1",
            "sfep-analysis-config/v1",
            "sfep-analysis-summary/v1",
            V1_ARTIFACT_VERSIONS,
            V1_SCHEMA_RESOURCES,
        ),
        (
            V2_CONTRACT,
            "sfep-equipment-bundle/v2",
            "sfep-analysis-config/v2",
            "sfep-analysis-summary/v2",
            V2_ARTIFACT_VERSIONS,
            V2_SCHEMA_RESOURCES,
        ),
    ],
)
def test_contract_profiles_pin_every_version_resource_and_digest(
    contract, manifest, config, summary, versions, resources
) -> None:
    assert contract.manifest_version == manifest
    assert contract.config_version == config
    assert contract.summary_version == summary
    assert contract.criteria_id_namespace == "sfep-criteria-id/v1"
    assert contract.bundle_id_namespace == "sfep-bundle-id/v1"
    assert dict(contract.artifact_versions) == versions
    assert set(contract.schema_resources) == ROLES
    assert {
        role: (resource.resource_path, resource.sha256)
        for role, resource in contract.schema_resources.items()
    } == resources
    assert all(
        resource.package == "equipment_quality"
        for resource in contract.schema_resources.values()
    )


def test_contract_profiles_and_nested_maps_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        V1_CONTRACT.manifest_version = "sfep-equipment-bundle/v2"  # type: ignore[misc]
    with pytest.raises(TypeError):
        V1_CONTRACT.artifact_versions["analysis_config"] = "forged"  # type: ignore[index]
    with pytest.raises(TypeError):
        V2_CONTRACT.schema_resources["producer_runtime"] = V2_CONTRACT.schema_resources[  # type: ignore[index]
            "analysis_config"
        ]
    with pytest.raises(FrozenInstanceError):
        V2_CONTRACT.schema_resources["analysis_config"].sha256 = "0" * 64  # type: ignore[misc]


@pytest.mark.parametrize(
    ("lookup", "version", "expected"),
    [
        (contract_for_manifest_version, "sfep-equipment-bundle/v1", V1_CONTRACT),
        (contract_for_manifest_version, "sfep-equipment-bundle/v2", V2_CONTRACT),
        (contract_for_config_version, "sfep-analysis-config/v1", V1_CONTRACT),
        (contract_for_config_version, "sfep-analysis-config/v2", V2_CONTRACT),
    ],
)
def test_contract_lookup_is_a_closed_allowlist(lookup, version, expected) -> None:
    assert lookup(version) is expected
    for unknown in ("", version + "/fallback", "v2", "sfep-equipment-bundle/v3"):
        with pytest.raises(ValueError, match="unsupported"):
            lookup(unknown)
    for invalid in (None, b"sfep-equipment-bundle/v1", True):
        with pytest.raises(TypeError, match="built-in string"):
            lookup(invalid)


def test_contract_schema_apis_authenticate_explicit_profile_resources() -> None:
    for contract, expected in (
        (V1_CONTRACT, V1_SCHEMA_RESOURCES),
        (V2_CONTRACT, V2_SCHEMA_RESOURCES),
    ):
        for role, (_path, digest) in expected.items():
            payload = contract_schema_bytes(contract, role)
            assert type(payload) is bytes
            assert hashlib.sha256(payload).hexdigest() == digest
    with pytest.raises(ValueError, match="unknown contract schema role"):
        contract_schema_bytes(V2_CONTRACT, "unknown")


def test_v2_reuse_is_explicit_and_v1_wrappers_keep_v1_meaning() -> None:
    for role in (
        "producer_runtime",
        "equipment_operating_ranges",
        "quality_risk_intervals",
        "replay_events",
    ):
        assert V2_CONTRACT.schema_resources[role] is V1_CONTRACT.schema_resources[role]

    config = json.loads(
        (Path(__file__).parents[1] / "analysis_config_v2.json").read_bytes()
    )
    validate_contract_instance(V2_CONTRACT, "analysis_config", config)
    assert normative_schema_bytes("analysis_config.schema.json") == contract_schema_bytes(
        V1_CONTRACT, "analysis_config"
    )
    with pytest.raises(ValidationError):
        validate_contract_instance(V1_CONTRACT, "analysis_config", config)


def test_bundle_request_selector_is_validated_defaults_to_v1_and_is_not_wire(
    tmp_path,
) -> None:
    request = bundle_request(tmp_path)
    assert request.contract is V1_CONTRACT
    assert request.manifest_version == "sfep-equipment-bundle/v1"
    for invalid in (None, b"sfep-equipment-bundle/v1", True):
        with pytest.raises(TypeError, match="built-in string"):
            replace(request, manifest_version=invalid)
    with pytest.raises(ValueError, match="does not match"):
        replace(request, manifest_version="sfep-equipment-bundle/v3")
    with pytest.raises(ValueError, match="does not match"):
        replace(request, manifest_version="sfep-equipment-bundle/v2")

    prepared = artifact_module._prepare_bundle(request)
    manifest = json.loads(prepared.manifest_bytes)
    assert manifest["schemaVersion"] == "sfep-equipment-bundle/v1"
    assert "manifestVersion" not in manifest


def test_selected_profile_rejects_cross_profile_schema_digest_mix(tmp_path) -> None:
    v1_request = bundle_request(tmp_path)
    v2_selector_with_v1_descriptors = replace(
        v1_request,
        contract=V2_CONTRACT,
        manifest_version="sfep-equipment-bundle/v2",
    )
    with pytest.raises(ValueError, match="analysis_config.*normative bytes"):
        artifact_module._prepare_bundle(v2_selector_with_v1_descriptors)


def test_default_v1_prepared_bytes_remain_frozen(tmp_path) -> None:
    prepared = artifact_module._prepare_bundle(bundle_request(tmp_path))
    actual = {
        **{
            artifact_module.FIXED_ARTIFACT_FILENAMES[role]: (
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            )
            for role, payload in prepared.artifact_bytes.items()
        },
        "bundle_manifest.json": (
            len(prepared.manifest_bytes),
            hashlib.sha256(prepared.manifest_bytes).hexdigest(),
        ),
    }
    assert actual == LITERAL_GOLDEN_ARTIFACT_SNAPSHOT
