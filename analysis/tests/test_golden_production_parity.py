"""Byte parity between the complete golden fixtures and Task 3-7 production."""

from __future__ import annotations

from pathlib import Path

from equipment_quality.deterministic import canonical_json_bytes
from equipment_quality.event_builder import build_replay_events, serialize_replay_events
from equipment_quality.feature_roles import definitions
from equipment_quality.genealogy import build_genealogy
from equipment_quality.operating_ranges import build_operating_ranges
from equipment_quality.quality_intervals import build_quality_rules
from equipment_quality.schema import load_analysis_config, read_inputs
from equipment_quality.time_split import build_time_split


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTATION_ROOT = REPOSITORY_ROOT / "contracts/equipment-monitor/v1/golden-expectation"
BUNDLE_ID = "sha256:" + "a" * 64
CRITERIA_ID = "sha256:" + "b" * 64


def _rendered_expectation(name: str) -> bytes:
    return (
        (EXPECTATION_ROOT / name)
        .read_bytes()
        .replace(b"@BUNDLE_ID@", BUNDLE_ID.encode("ascii"))
        .replace(b"@CRITERIA_ID@", CRITERIA_ID.encode("ascii"))
    )


def test_full_golden_ranges_rules_and_replay_match_production_bytes():
    config = load_analysis_config(REPOSITORY_ROOT / "analysis/analysis_config.json")
    inputs = read_inputs(REPOSITORY_ROOT / "contracts/equipment-monitor/v1/golden-source")
    genealogy = build_genealogy(inputs)
    split = build_time_split(genealogy, config)
    configured = definitions(config)

    assert (len(genealogy.boundary_rows), len(genealogy.replay_rows), len(genealogy.quality_rows)) == (
        12, 12, 11,
    )
    assert (split.as_of.isoformat(), split.discovery_cutoff.isoformat()) == (
        "2025-02-20", "2025-01-03",
    )
    assert tuple(
        len(rows) for rows in (
            split.reference_rows, split.discovery_rows,
            split.confirmation_rows, split.holdout_rows,
        )
    ) == (10, 5, 2, 2)

    ranges = canonical_json_bytes({
        "asOf": split.as_of.isoformat(),
        "criteriaId": CRITERIA_ID,
        "ranges": build_operating_ranges(split, configured, config),
        "schemaVersion": "sfep-operating-ranges/v1",
    })
    rules = canonical_json_bytes({
        "asOf": split.as_of.isoformat(),
        "criteriaId": CRITERIA_ID,
        "rules": build_quality_rules(split, configured, config, CRITERIA_ID),
        "schemaVersion": "sfep-quality-rules/v1",
    })
    replay = serialize_replay_events(
        build_replay_events(genealogy, BUNDLE_ID, CRITERIA_ID, config)
    )

    assert ranges == _rendered_expectation("equipment_operating_ranges.template.json")
    assert rules == _rendered_expectation("quality_risk_intervals.template.json")
    assert replay == _rendered_expectation("replay_events.template.csv")
