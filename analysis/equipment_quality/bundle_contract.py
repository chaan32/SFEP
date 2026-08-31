"""Closed, immutable bundle-contract profiles for trusted publication."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


SCHEMA_ROLES = frozenset(
    {
        "analysis_config",
        "analysis_summary",
        "bundle_manifest",
        "equipment_operating_ranges",
        "producer_runtime",
        "quality_risk_intervals",
        "replay_events",
    }
)
ARTIFACT_ROLES = frozenset(SCHEMA_ROLES - {"bundle_manifest"})


@dataclass(frozen=True)
class SchemaResource:
    """One digest-authenticated schema at an explicit package resource path."""

    package: str
    resource_path: str
    sha256: str

    def __post_init__(self) -> None:
        if type(self.package) is not str or self.package != "equipment_quality":
            raise ValueError("schema resource package must be equipment_quality")
        if (
            type(self.resource_path) is not str
            or not self.resource_path.startswith("contracts/")
            or not self.resource_path.endswith(".schema.json")
            or ".." in self.resource_path.split("/")
        ):
            raise ValueError("schema resource path must be a packaged contract schema")
        if (
            type(self.sha256) is not str
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("schema resource sha256 must be a lowercase hex digest")


@dataclass(frozen=True)
class BundleContract:
    manifest_version: str
    config_version: str
    summary_version: str
    artifact_versions: Mapping[str, str]
    schema_resources: Mapping[str, SchemaResource]
    criteria_id_namespace: str = "sfep-criteria-id/v1"
    bundle_id_namespace: str = "sfep-bundle-id/v1"

    def __post_init__(self) -> None:
        for label, value in (
            ("manifest_version", self.manifest_version),
            ("config_version", self.config_version),
            ("summary_version", self.summary_version),
            ("criteria_id_namespace", self.criteria_id_namespace),
            ("bundle_id_namespace", self.bundle_id_namespace),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"{label} must be a built-in non-empty string")
        if self.criteria_id_namespace != "sfep-criteria-id/v1":
            raise ValueError("criteria ID namespace must remain sfep-criteria-id/v1")
        if self.bundle_id_namespace != "sfep-bundle-id/v1":
            raise ValueError("bundle ID namespace must remain sfep-bundle-id/v1")

        artifact_versions = dict(self.artifact_versions)
        if set(artifact_versions) != ARTIFACT_ROLES or any(
            type(version) is not str or not version
            for version in artifact_versions.values()
        ):
            raise ValueError("artifact versions must own the exact six artifact roles")
        schema_resources = dict(self.schema_resources)
        if set(schema_resources) != SCHEMA_ROLES or any(
            type(resource) is not SchemaResource
            for resource in schema_resources.values()
        ):
            raise ValueError("schema resources must own the exact seven schema roles")
        if artifact_versions["analysis_config"] != self.config_version:
            raise ValueError("config artifact version must match the contract")
        if artifact_versions["analysis_summary"] != self.summary_version:
            raise ValueError("summary artifact version must match the contract")

        object.__setattr__(
            self, "artifact_versions", MappingProxyType(artifact_versions)
        )
        object.__setattr__(
            self, "schema_resources", MappingProxyType(schema_resources)
        )


def _resource(version: str, name: str, digest: str) -> SchemaResource:
    return SchemaResource(
        package="equipment_quality",
        resource_path=f"contracts/{version}/{name}",
        sha256=digest,
    )


_V1_RESOURCES = MappingProxyType(
    {
        "analysis_config": _resource(
            "v1",
            "analysis_config.schema.json",
            "fad28561dfe9d9fe3cd09b025bb18c2101be053cb094b08442ea45963b86f549",
        ),
        "analysis_summary": _resource(
            "v1",
            "analysis_summary.schema.json",
            "6cb6077cc7093b90e464cc794604ca6b7682002a138ebed051801aee29f9d374",
        ),
        "bundle_manifest": _resource(
            "v1",
            "bundle_manifest.schema.json",
            "666e880d296c0d7e3df5af1aa80e6865922ebfb337fb9aca48f93eddfd89e8a5",
        ),
        "equipment_operating_ranges": _resource(
            "v1",
            "equipment_operating_ranges.schema.json",
            "bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89",
        ),
        "producer_runtime": _resource(
            "v1",
            "producer_runtime.schema.json",
            "97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6",
        ),
        "quality_risk_intervals": _resource(
            "v1",
            "quality_risk_intervals.schema.json",
            "2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad",
        ),
        "replay_events": _resource(
            "v1",
            "replay_event_row.schema.json",
            "309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6",
        ),
    }
)

V1_CONTRACT = BundleContract(
    manifest_version="sfep-equipment-bundle/v1",
    config_version="sfep-analysis-config/v1",
    summary_version="sfep-analysis-summary/v1",
    artifact_versions={
        "analysis_config": "sfep-analysis-config/v1",
        "producer_runtime": "sfep-producer-runtime/v1",
        "equipment_operating_ranges": "sfep-operating-ranges/v1",
        "quality_risk_intervals": "sfep-quality-rules/v1",
        "replay_events": "sfep-replay-events/v1",
        "analysis_summary": "sfep-analysis-summary/v1",
    },
    schema_resources=_V1_RESOURCES,
)

V2_CONTRACT = BundleContract(
    manifest_version="sfep-equipment-bundle/v2",
    config_version="sfep-analysis-config/v2",
    summary_version="sfep-analysis-summary/v2",
    artifact_versions={
        "analysis_config": "sfep-analysis-config/v2",
        "producer_runtime": "sfep-producer-runtime/v1",
        "equipment_operating_ranges": "sfep-operating-ranges/v1",
        "quality_risk_intervals": "sfep-quality-rules/v1",
        "replay_events": "sfep-replay-events/v1",
        "analysis_summary": "sfep-analysis-summary/v2",
    },
    schema_resources={
        "analysis_config": _resource(
            "v2",
            "analysis_config.schema.json",
            "7942b12abcfeee661290f5b40df922edfeec66d6f95dc1ce20e78cc0f58f5da1",
        ),
        "analysis_summary": _resource(
            "v2",
            "analysis_summary.schema.json",
            "0b38234ea0b7c5614dff0ac7514b6fb82e401a7846621e45bcbcf00ed82dc7c4",
        ),
        "bundle_manifest": _resource(
            "v2",
            "bundle_manifest.schema.json",
            "0d7c4fb5e939beb324c6d83005a83cb9702e480a5babdfede88e5ca05776c2c3",
        ),
        # Reuse is explicit: these are the authenticated v1 resource objects.
        "equipment_operating_ranges": _V1_RESOURCES["equipment_operating_ranges"],
        "producer_runtime": _V1_RESOURCES["producer_runtime"],
        "quality_risk_intervals": _V1_RESOURCES["quality_risk_intervals"],
        "replay_events": _V1_RESOURCES["replay_events"],
    },
)

_BY_MANIFEST_VERSION = MappingProxyType(
    {
        V1_CONTRACT.manifest_version: V1_CONTRACT,
        V2_CONTRACT.manifest_version: V2_CONTRACT,
    }
)
_BY_CONFIG_VERSION = MappingProxyType(
    {
        V1_CONTRACT.config_version: V1_CONTRACT,
        V2_CONTRACT.config_version: V2_CONTRACT,
    }
)


def _lookup_contract(
    profiles: Mapping[str, BundleContract], version: object, label: str
) -> BundleContract:
    if type(version) is not str:
        raise TypeError(f"{label} version must be a built-in string")
    try:
        return profiles[version]
    except KeyError as error:
        raise ValueError(f"unsupported {label} version: {version!r}") from error


def contract_for_manifest_version(version: object) -> BundleContract:
    """Select one exact supported manifest contract, with no fallback."""
    return _lookup_contract(_BY_MANIFEST_VERSION, version, "manifest")


def contract_for_config_version(version: object) -> BundleContract:
    """Select one exact supported config contract, with no fallback."""
    return _lookup_contract(_BY_CONFIG_VERSION, version, "config")
