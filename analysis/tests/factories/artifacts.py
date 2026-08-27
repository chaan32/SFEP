"""Independent literal fixtures for Task 8 artifact contracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import date
from functools import lru_cache
from pathlib import Path
import json

from equipment_quality.feature_roles import FeatureDefinition, definitions
from equipment_quality.genealogy import build_genealogy
from equipment_quality.deterministic import canonical_json_bytes, sha256_uri
from equipment_quality.event_builder import build_replay_events
from equipment_quality.models import (
    BundleWriteRequest,
    GenealogyResult,
    OperatingRangeSidecar,
    OperatingRangesResult,
    SourceFile,
    SummaryBuildRequest,
    TimeSplitResult,
)
from equipment_quality.operating_ranges import build_operating_ranges_result
from equipment_quality.quality_intervals import build_quality_rules_result
from equipment_quality.schema import (
    load_analysis_config,
    normative_schema_bytes,
    read_inputs,
)
from equipment_quality.time_split import build_time_split
from equipment_quality.artifacts import compute_bundle_identity, compute_criteria_identity
from equipment_quality.criteria_projection import build_criteria_projection


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = REPOSITORY_ROOT / "contracts" / "equipment-monitor" / "v1"

LITERAL_CRITERIA_SCHEMA_DIGESTS = {
    "analysis_config": "sha256:" + "1" * 64,
    "equipment_operating_ranges": "sha256:" + "2" * 64,
    "producer_runtime": "sha256:" + "3" * 64,
    "quality_risk_intervals": "sha256:" + "4" * 64,
}
LITERAL_BUNDLE_SCHEMA_DIGESTS = {
    **LITERAL_CRITERIA_SCHEMA_DIGESTS,
    "analysis_summary": "sha256:" + "8" * 64,
    "bundle_manifest": "sha256:" + "9" * 64,
    "replay_events": "sha256:" + "a" * 64,
}
LITERAL_EIGHT_CRITERIA_FIELDS = {
    "analysis_config_sha256": "sha256:f612b89bcdbc401379f644d7e48572e3470f77dcd4c39416405d80952ad7089e",
    "as_of": "2025-02-20",
    "criteria_projection_sha256": "sha256:3407e40a0dcb37d9c7a207e5a26a1b0cc94adcb1fb9db2a3dee62ee2b5f2f89c",
    "producer_runtime_sha256": "sha256:fae9d8f386d67956867dedef7c89476199a4a25ee9ffe13560a6bfae7ae6c407",
    "schema.analysis_config.sha256": "sha256:" + "1" * 64,
    "schema.equipment_operating_ranges.sha256": "sha256:" + "2" * 64,
    "schema.producer_runtime.sha256": "sha256:" + "3" * 64,
    "schema.quality_risk_intervals.sha256": "sha256:" + "4" * 64,
}
LITERAL_CRITERIA_ID = (
    "sha256:c991dee27eb2274eb7a169ca4ee761a7dca976020ee0508c0875cb3fe7df4f0c"
)
LITERAL_NINETEEN_BUNDLE_FIELDS = {
    "analysis_config_sha256": LITERAL_EIGHT_CRITERIA_FIELDS["analysis_config_sha256"],
    "criteria_id": LITERAL_CRITERIA_ID,
    "producer_runtime_sha256": LITERAL_EIGHT_CRITERIA_FIELDS["producer_runtime_sha256"],
    "schema.analysis_config.sha256": "sha256:" + "1" * 64,
    "schema.analysis_summary.sha256": "sha256:" + "8" * 64,
    "schema.bundle_manifest.sha256": "sha256:" + "9" * 64,
    "schema.equipment_operating_ranges.sha256": "sha256:" + "2" * 64,
    "schema.producer_runtime.sha256": "sha256:" + "3" * 64,
    "schema.quality_risk_intervals.sha256": "sha256:" + "4" * 64,
    "schema.replay_events.sha256": "sha256:" + "a" * 64,
    "source.ap.name": "sts_3ap_3.csv",
    "source.ap.sha256": "sha256:" + "7" * 64,
    "source.ap.size_bytes": "33",
    "source.fur_hr.name": "sts_2fur_hr_2.csv",
    "source.fur_hr.sha256": "sha256:" + "6" * 64,
    "source.fur_hr.size_bytes": "22",
    "source.sm_cc.name": "sts_1sm_cc_1.csv",
    "source.sm_cc.sha256": "sha256:" + "5" * 64,
    "source.sm_cc.size_bytes": "11",
}
LITERAL_BUNDLE_ID = (
    "sha256:a18b06143e1d7c5e138ee62da061c3c8ed69f2b4d4fd5a79134dd4d4acbf377c"
)
LITERAL_GOLDEN_BUNDLE_ID = (
    "sha256:d823933a151d23c5c134d67aa93140a76b41a78ab3d9b3fd2e6dade6c2e54757"
)
LITERAL_GOLDEN_ARTIFACT_SNAPSHOT = {
    "analysis_config.json": (
        13_830,
        "bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b",
    ),
    "producer_runtime.json": (
        1_833,
        "e1b4066209d20764c007db58284483a2c3bc3b1e3fea5083bca17e97d4ef2ac0",
    ),
    "equipment_operating_ranges.json": (
        164,
        "3aa2cb1b42afda6b8c6b3aa3f02d0efa25f8a9e331c9eaa20c24962c4cac6aa8",
    ),
    "quality_risk_intervals.json": (
        260_330,
        "767a492fbaa7bbc73e70c16698063e53dcf96b8203b77d30835e9ef59d1082cc",
    ),
    "replay_events.csv": (
        59_114,
        "872c1faf4841f21c1a5c0b579d43d5b4ed575b89454a63e2cb061aabe5311e58",
    ),
    "analysis_summary.json": (
        383_818,
        "3e9d424605a4d4c77f48e4968a4312cce6cfc6a4116422746ff8b6c916eba406",
    ),
    "bundle_manifest.json": (
        3_676,
        "fe40f501b9c1ea8d6d3c3440f885b1ec3053882a8596a32f718413b4bf9d500e",
    ),
}


def literal_criteria_identity_inputs() -> dict[str, object]:
    return {
        "as_of": date(2025, 2, 20),
        "criteria_projection": b"projection\n",
        "analysis_config": b"config\n",
        "producer_runtime": b"runtime\n",
        "schema_digests": dict(LITERAL_CRITERIA_SCHEMA_DIGESTS),
    }


def literal_bundle_identity_inputs() -> dict[str, object]:
    return {
        "criteria_id": LITERAL_CRITERIA_ID,
        "analysis_config": b"config\n",
        "producer_runtime": b"runtime\n",
        "sources": (
            SourceFile("sm_cc", "sts_1sm_cc_1.csv", 11, "sha256:" + "5" * 64),
            SourceFile("fur_hr", "sts_2fur_hr_2.csv", 22, "sha256:" + "6" * 64),
            SourceFile("ap", "sts_3ap_3.csv", 33, "sha256:" + "7" * 64),
        ),
        "schema_digests": dict(LITERAL_BUNDLE_SCHEMA_DIGESTS),
    }


class StatefulMapping(Mapping[str, str]):
    """Expose a different item sequence after the first snapshot."""

    def __init__(
        self,
        first: Mapping[str, str],
        later: Mapping[str, str],
    ) -> None:
        self._first = tuple(first.items())
        self._later = tuple(later.items())
        self._calls = 0

    def __getitem__(self, key: str) -> str:
        return dict(self._first)[key]

    def __iter__(self) -> Iterator[str]:
        return iter(dict(self._first))

    def __len__(self) -> int:
        return len(self._first)

    def items(self):  # type: ignore[override]
        self._calls += 1
        return self._first if self._calls == 1 else self._later


def golden_split_and_definitions() -> tuple[TimeSplitResult, tuple[FeatureDefinition, ...]]:
    config = load_analysis_config(REPOSITORY_ROOT / "analysis" / "analysis_config.json")
    inputs = read_inputs(CONTRACT_ROOT / "golden-source")
    split = build_time_split(build_genealogy(inputs), config)
    return split, definitions(config)


def _copy_split(split: TimeSplitResult, **frames) -> TimeSplitResult:
    return TimeSplitResult(
        as_of=split.as_of,
        discovery_cutoff=split.discovery_cutoff,
        reference_rows=frames.get("reference_rows", split.reference_rows.copy(deep=True)),
        discovery_rows=frames.get("discovery_rows", split.discovery_rows.copy(deep=True)),
        confirmation_rows=frames.get(
            "confirmation_rows", split.confirmation_rows.copy(deep=True)
        ),
        holdout_rows=frames.get("holdout_rows", split.holdout_rows.copy(deep=True)),
        counts=split.counts,
    )


def mutate_holdout(split: TimeSplitResult) -> TimeSplitResult:
    holdout = split.holdout_rows.copy(deep=True)
    for field in ("f_pre_temp", "slab_grind", "judge"):
        holdout[field] = [999999.0, -999999.0] if field == "f_pre_temp" else ["X", "Y"]
    return _copy_split(split, holdout_rows=holdout)


def mutate_mature_feature(split: TimeSplitResult) -> TimeSplitResult:
    discovery = split.discovery_rows.copy(deep=True)
    discovery.loc[discovery.index[0], "f_pre_temp"] = 9876.5
    return _copy_split(split, discovery_rows=discovery)


def mutate_mature_label(split: TimeSplitResult) -> TimeSplitResult:
    confirmation = split.confirmation_rows.copy(deep=True)
    confirmation.loc[confirmation.index[0], "judge"] = "불량"
    return _copy_split(split, confirmation_rows=confirmation)


def definitions_with_stage_change(
    items: tuple[FeatureDefinition, ...],
) -> tuple[FeatureDefinition, ...]:
    changed = []
    for item in items:
        if item.name == "f_pre_temp":
            changed.append(replace(item, first_stage="FURNACE_CHARGED"))
        else:
            changed.append(item)
    return tuple(changed)


def runtime_payload() -> dict[str, object]:
    digest = "sha256:" + "b" * 64
    return {
        "schemaVersion": "sfep-producer-runtime/v1",
        "platform": {
            "system": "Darwin",
            "machine": "arm64",
            "macosProductVersion": "15.6",
            "sysconfigPlatform": "macosx-15.0-arm64",
        },
        "python": {
            "implementation": "CPython",
            "version": "3.12.10",
            "build": "main, Apr  8 2025, 12:00:00",
            "cacheTag": "cpython-312",
            "soabi": "cpython-312-darwin",
            "executableSha256": digest,
        },
        "pipVersion": "25.1.1",
        "locks": {
            "pyproject": digest,
            "bootstrap": digest,
            "buildRequirements": digest,
            "requirements": digest,
            "wheelhouse": digest,
            "producer": digest,
        },
        "packages": [
            {
                "name": "jsonschema",
                "version": "4.24.0",
                "direct": True,
                "wheelFilename": "jsonschema-4.24.0-py3-none-any.whl",
                "wheelTag": "py3-none-any",
                "wheelSha256": digest,
                "installedCodeTreeSha256": digest,
            }
        ],
        "producer": {
            "name": "equipment-quality",
            "version": "0.1.0",
            "wheelFilename": "equipment_quality-0.1.0-py3-none-any.whl",
            "wheelSha256": digest,
            "installedCodeTreeSha256": digest,
            "sourceSha256": digest,
        },
        "environmentPolicy": {
            "pythonHashSeed": "0",
            "timezone": "Asia/Seoul",
            "localeIndependentParsing": True,
            "floatPolicy": "IEEE754_BINARY64_FINITE",
        },
    }


def normative_schema_digests() -> dict[str, str]:
    names = {
        "bundle_manifest": "bundle_manifest.schema.json",
        "analysis_config": "analysis_config.schema.json",
        "producer_runtime": "producer_runtime.schema.json",
        "equipment_operating_ranges": "equipment_operating_ranges.schema.json",
        "quality_risk_intervals": "quality_risk_intervals.schema.json",
        "analysis_summary": "analysis_summary.schema.json",
        "replay_events": "replay_event_row.schema.json",
    }
    return {role: sha256_uri(normative_schema_bytes(name)) for role, name in names.items()}


def golden_summary_request(*, return_aliases: bool = False):
    config_path = REPOSITORY_ROOT / "analysis" / "analysis_config.json"
    config_bytes = config_path.read_bytes()
    runtime_bytes = canonical_json_bytes(runtime_payload())
    config = load_analysis_config(config_path)
    inputs = read_inputs(CONTRACT_ROOT / "golden-source")
    genealogy = build_genealogy(inputs)
    split = build_time_split(genealogy, config)
    items = definitions(config)
    material_catalog = tuple(genealogy.lineage_rows["material_lineage"].tolist())
    schema_digests = normative_schema_digests()
    projection = build_criteria_projection(split, items)
    criteria_identity = compute_criteria_identity(
        split.as_of,
        projection,
        config_bytes,
        runtime_bytes,
        {role: schema_digests[role] for role in LITERAL_CRITERIA_SCHEMA_DIGESTS},
    )
    bundle_identity = compute_bundle_identity(
        criteria_identity.value,
        config_bytes,
        runtime_bytes,
        inputs.sources,
        schema_digests,
    )
    ranges = build_operating_ranges_result(
        split, items, config, material_catalog=material_catalog
    )
    rules = build_quality_rules_result(
        split,
        items,
        config,
        criteria_identity.value,
        material_catalog=material_catalog,
    )
    events = tuple(
        build_replay_events(
            genealogy,
            bundle_identity.value,
            criteria_identity.value,
            config,
        )
    )
    request = SummaryBuildRequest(
        analysis_config=config,
        analysis_config_bytes=config_bytes,
        producer_runtime_bytes=runtime_bytes,
        schema_digests=schema_digests,
        inputs=inputs,
        genealogy=genealogy,
        split=split,
        material_catalog=material_catalog,
        definitions=items,
        criteria_identity=criteria_identity,
        bundle_identity=bundle_identity,
        operating_ranges=ranges,
        quality_rules=rules,
        events=events,
    )
    return (request, inputs) if return_aliases else request


def nonempty_range_summary_request() -> SummaryBuildRequest:
    """Return a golden-shaped request with one literal rich range sidecar."""
    request = golden_summary_request()
    row = request.split.reference_rows.iloc[0]
    contributor = next(
        material
        for material in request.material_catalog
        if (
            material.charge_id,
            material.slab_no,
            material.hr_coil_id,
        )
        == (row["charge_id"], row["slab_no"], row["hr_coil_id"])
    )
    rule_id = "sha256:" + "b" * 64
    ranges = OperatingRangesResult(
        (
            {
                "ruleId": rule_id,
                "field": "f_pre_temp",
                "fieldRole": "DIRECT_OPERATION",
                "firstAvailableStage": "PREHEAT_COMPLETE",
                "equipmentType": "FURNACE",
                "equipmentId": "1",
                "contextLevel": 0,
                "context": {
                    "furnace_no": "1호기",
                    "f_jangip_gubun": "DIRECT",
                    "slab_width_band": "Q2",
                    "steel_grade": "S1",
                    "steel_usage": "U1",
                },
                "support": 1,
                "median": 1120.0,
                "p01": None,
                "p05": 1110.0,
                "p95": 1130.0,
                "p99": None,
                "lowerTailEnabled": False,
                "upperTailEnabled": False,
            },
        ),
        (OperatingRangeSidecar(rule_id, (contributor.material_key,)),),
    )
    counts = dict(request.split.counts)
    counts["labelCensoring"] = {"LABEL_MISSING": 1}
    split = replace(request.split, counts=counts)
    audit = dict(request.genealogy.audit)
    audit["quarantine"] = {"MISSING_SM_CC_KEY": 1}
    genealogy = GenealogyResult(
        boundary_rows=request.genealogy.boundary_rows.copy(deep=True),
        replay_rows=request.genealogy.replay_rows.copy(deep=True),
        quality_rows=request.genealogy.quality_rows.copy(deep=True),
        quarantine_rows=request.genealogy.quarantine_rows.copy(deep=True),
        lineage_rows=request.genealogy.lineage_rows.copy(deep=True),
        audit=audit,
    )
    return replace(
        request,
        split=split,
        genealogy=genealogy,
        operating_ranges=ranges,
    )


def empty_context_range_summary_request() -> SummaryBuildRequest:
    """Return one valid RM4 fallback range whose context object is empty."""
    request = golden_summary_request()
    row = request.split.reference_rows.iloc[0]
    contributor = next(
        material
        for material in request.material_catalog
        if (
            material.charge_id,
            material.slab_no,
            material.hr_coil_id,
        )
        == (row["charge_id"], row["slab_no"], row["hr_coil_id"])
    )
    rule_id = "sha256:" + "c" * 64
    ranges = OperatingRangesResult(
        (
            {
                "ruleId": rule_id,
                "field": "rm4_temp",
                "fieldRole": "DIRECT_OPERATION",
                "firstAvailableStage": "RM4_RECORDED",
                "equipmentType": "RM4",
                "equipmentId": "RM4_PROCESS",
                "contextLevel": 3,
                "context": {},
                "support": 1,
                "median": 850.0,
                "p01": None,
                "p05": 845.0,
                "p95": 855.0,
                "p99": None,
                "lowerTailEnabled": False,
                "upperTailEnabled": False,
            },
        ),
        (OperatingRangeSidecar(rule_id, (contributor.material_key,)),),
    )
    return replace(request, operating_ranges=ranges)


def expected_golden_summary_bytes(request: SummaryBuildRequest) -> bytes:
    template = (
        CONTRACT_ROOT / "golden-expectation" / "analysis_summary.template.json"
    ).read_bytes()
    return template.replace(
        b"@CRITERIA_ID@", request.criteria_identity.value.encode("ascii")
    ).replace(b"@BUNDLE_ID@", request.bundle_identity.value.encode("ascii"))


def holdout_rule(
    *,
    grade: str = "DANGER",
    stage: str = "PREHEAT_COMPLETE",
    lower: float = 1110.0,
    upper: float = 1130.0,
) -> dict[str, object]:
    return {
        "grade": grade,
        "earlyWarningEligible": stage != "AP_RECORDED_WITH_RESULT",
        "firstAvailableStage": stage,
        "applicationContext": {},
        "predicate": {
            "allOf": [
                {
                    "field": "f_pre_temp",
                    "type": "NUMERIC_INTERVAL",
                    "lower": lower,
                    "lowerInclusive": True,
                    "upper": upper,
                    "upperInclusive": True,
                    "values": None,
                }
            ]
        },
    }


@lru_cache(maxsize=1)
def _golden_bundle_payload() -> tuple[SummaryBuildRequest, dict[str, object]]:
    from equipment_quality.summary import build_summary

    request = golden_summary_request()
    return request, build_summary(request)


def bundle_request(
    output_root: Path,
    *,
    analysis_summary: Mapping[str, object] | None = None,
    quality_schema_version: str = "sfep-quality-rules/v1",
) -> BundleWriteRequest:
    request, frozen_summary = _golden_bundle_payload()
    return BundleWriteRequest(
        output_root=output_root,
        analysis_config=request.analysis_config_bytes,
        producer_runtime=request.producer_runtime_bytes,
        equipment_operating_ranges={
            "asOf": request.split.as_of.isoformat(),
            "criteriaId": request.criteria_identity.value,
            "ranges": request.operating_ranges.to_wire(),
            "schemaVersion": "sfep-operating-ranges/v1",
        },
        quality_risk_intervals={
            "asOf": request.split.as_of.isoformat(),
            "criteriaId": request.criteria_identity.value,
            "rules": request.quality_rules.to_wire(),
            "schemaVersion": quality_schema_version,
        },
        replay_events=request.events,
        analysis_summary=(
            dict(analysis_summary)
            if analysis_summary is not None
            else frozen_summary
        ),
        criteria_identity=request.criteria_identity,
        bundle_identity=request.bundle_identity,
        sources=request.inputs.sources,
        schema_digests=request.schema_digests,
        as_of=request.split.as_of,
        timezone=request.analysis_config.timezone,
        label_maturity_days=request.analysis_config.label_maturity_days,
    )
