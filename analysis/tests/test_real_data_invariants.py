"""Real steel snapshot and sealed-producer invariants."""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import io
from pathlib import Path

import pytest


pytestmark = pytest.mark.real_data


_EXPECTED_SOURCES = {
    "sts_1sm_cc_1.csv": {
        "size": 1_883_100,
        "sha256": "0cd3e91428c005dae1785b9af01d30e3d08230e2058c68693c9aa7ffee043075",
        "shape": (23_649, 15),
        "header": (
            "sm_plant", "charge_id", "steel_grade", "steel_usage",
            "delta_ferrite", "ingre_cr", "ingre_ni", "ingre_s", "cast_date",
            "cc_gubun", "tundish_temp", "mlac_ratio", "slab_no",
            "slab_gubun", "slab_grind",
        ),
    },
    "sts_2fur_hr_2.csv": {
        "size": 3_578_800,
        "sha256": "c2bb0b503ec30b0e01e00d2bd88fde536479de59aebf1eae84131380d58f3bcf",
        "shape": (23_652, 26),
        "header": (
            "charge_id", "slab_no", "furnace_no", "f_jangip_gubun",
            "f_jangip_temp", "f_bfg", "f_cog", "f_ldg", "f_bfg_per",
            "f_cog_per", "f_ldg_per", "f_pre_temp", "f_heat_temp",
            "f_sock_temp", "f_pre_interval", "f_heat_interval",
            "f_sock_interval", "f_ext_date", "f_ext_time", "hr_coil_id",
            "hr_date", "hr_thick", "hr_width", "rm4_temp", "rm_pitch",
            "slab_width",
        ),
    },
    "sts_3ap_3.csv": {
        "size": 1_277_032,
        "sha256": "ff4572a1302459787ddca7458857864182d969e45b6a81edc4241bc47549801d",
        "shape": (23_641, 9),
        "header": (
            "judge", "hr_coil_id", "ap_plant", "ap_prod_id", "ap_date",
            "ap_shift", "ap_thick", "ap_width", "ap_line_speed",
        ),
    },
}


def _strict_cp949_rows(payload: bytes) -> list[list[str]]:
    return list(
        csv.reader(io.StringIO(payload.decode("cp949"), newline=""), strict=True)
    )


def _independent_id(version: str, fields: dict[str, str]) -> str:
    lines = [version]
    lines.extend(
        f"{key}={value}"
        for key, value in sorted(fields.items(), key=lambda item: item[0].encode("utf-8"))
    )
    preimage = ("\n".join(lines) + "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(preimage).hexdigest()


def _assert_manifest_sources_match_bytes(
    manifest: dict[str, object],
    data_dir: Path,
) -> None:
    role_names = {
        "sm_cc": "sts_1sm_cc_1.csv",
        "fur_hr": "sts_2fur_hr_2.csv",
        "ap": "sts_3ap_3.csv",
    }
    identity = manifest["identity"]
    assert isinstance(identity, dict)
    for role, name in role_names.items():
        payload = (data_dir / name).read_bytes()
        assert identity[f"source.{role}.name"] == name
        assert identity[f"source.{role}.size_bytes"] == str(len(payload))
        assert identity[f"source.{role}.sha256"] == (
            "sha256:" + hashlib.sha256(payload).hexdigest()
        )


@pytest.mark.parametrize("injection_point", ["before", "during"])
def test_sealed_runner_rejects_injected_bytecode_cache(
    tmp_path,
    monkeypatch,
    sealed_harness_module,
    injection_point,
) -> None:
    venv = tmp_path / "venv"
    runtime = venv / "bin" / "python"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"test executable placeholder")
    (venv / "pyvenv.cfg").write_text("version = 3.12.10\n", encoding="utf-8")
    site_packages = venv / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    controller = tmp_path / "controller"
    controller.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    poisoned = site_packages / "injected.pyc"

    if injection_point == "before":
        poisoned.write_bytes(b"poison")

    def injected_runner(*_args, **_kwargs):
        if injection_point == "during":
            poisoned.write_bytes(b"poison")
        return sealed_harness_module._SealedResult(0, b"", b"")

    monkeypatch.setattr(
        sealed_harness_module,
        "_bounded_subprocess",
        injected_runner,
    )
    harness = sealed_harness_module._RealHarness(
        data_dir=data_dir,
        runtimes=(runtime, runtime),
        controller_dir=controller,
    )
    with pytest.raises(AssertionError, match="bytecode cache"):
        harness._run(runtime, [])


def test_raw_real_snapshot_is_frozen_by_independent_stdlib_oracle(real_harness) -> None:
    csv_paths = sorted(real_harness.data_dir.glob("*.csv"), key=lambda path: path.name)
    assert [path.name for path in csv_paths] == sorted(_EXPECTED_SOURCES)

    parsed: dict[str, list[list[str]]] = {}
    for path in csv_paths:
        expected = _EXPECTED_SOURCES[path.name]
        payload = path.read_bytes()
        rows = _strict_cp949_rows(payload)
        parsed[path.name] = rows
        assert len(payload) == expected["size"]
        assert hashlib.sha256(payload).hexdigest() == expected["sha256"]
        assert tuple(rows[0]) == expected["header"]
        assert (len(rows) - 1, len(rows[0])) == expected["shape"]
        assert all(len(row) == len(rows[0]) for row in rows[1:])

    furnace = parsed["sts_2fur_hr_2.csv"]
    furnace_column = furnace[0].index("furnace_no")
    assert {row[furnace_column] for row in furnace[1:]} == {
        "1호기", "2호기", "3호기", "4호기",
    }
    ap = parsed["sts_3ap_3.csv"]
    judge_column = ap[0].index("judge")
    assert Counter(row[judge_column] for row in ap[1:]) == {
        "양품": 23_010,
        "불량": 631,
    }


def test_sealed_runtime_probe_reports_provenance_shapes(real_harness) -> None:
    """The sealed producer must add one source-record provenance column."""
    assert real_harness.probe_input_shapes() == {
        "provenanceColumns": {
            "ap": ["_source_record_number"],
            "fur_hr": ["_source_record_number"],
            "sm_cc": ["_source_record_number"],
        },
        "shapes": {
            "ap": [23641, 10],
            "fur_hr": [23652, 27],
            "sm_cc": [23649, 16],
        },
    }


def test_controller_uses_only_the_allowlisted_sealed_subprocess_contract(
    real_harness,
) -> None:
    venv_roots = tuple(runtime.parent.parent for runtime in real_harness.runtimes)
    site_roots = tuple(
        root / "lib" / "python3.12" / "site-packages" for root in venv_roots
    )
    assert venv_roots[0] != venv_roots[1]
    assert site_roots[0] != site_roots[1]
    assert not site_roots[0].samefile(site_roots[1])
    for runtime in real_harness.runtimes:
        command, environment = real_harness.controller_contract(runtime)
        assert command[0] == str(runtime)
        assert command[1:5] == ["-P", "-s", "-S", "-B"]
        assert "-I" not in command
        assert command[5] == "-c"
        assert command[7] == str(
            runtime.parent.parent / "lib" / "python3.12" / "site-packages"
        )
        assert environment == {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "Asia/Seoul",
        }
        assert "PYTHONPATH" not in environment


def test_two_sealed_runtimes_publish_the_same_authenticated_bundle(
    real_harness,
    baseline_runs,
) -> None:
    first, second = baseline_runs.runs
    expected_names = {
        "analysis_config.json",
        "producer_runtime.json",
        "equipment_operating_ranges.json",
        "quality_risk_intervals.json",
        "replay_events.csv",
        "analysis_summary.json",
        "bundle_manifest.json",
    }
    assert first.bundle_id == second.bundle_id
    assert first.snapshot == second.snapshot
    assert set(first.snapshot) == set(second.snapshot) == expected_names

    for run in (first, second):
        manifest = run.json_payloads["bundle_manifest.json"]
        _assert_manifest_sources_match_bytes(manifest, real_harness.data_dir)
        assert run.bundle_root.name == manifest["bundleId"] == run.bundle_id
        assert manifest["criteriaId"] == manifest["identity"]["criteria_id"]
        assert len(manifest["criteriaIdentity"]) == 8
        assert manifest["criteriaId"] == _independent_id(
            "sfep-criteria-id/v1", manifest["criteriaIdentity"]
        )
        assert manifest["bundleId"] == _independent_id(
            "sfep-bundle-id/v1", manifest["identity"]
        )
        assert manifest["schemaVersion"] == "sfep-equipment-bundle/v1"
        assert len(manifest["artifacts"]) == 6
        expected_artifacts = {
            "analysis_config": ("analysis_config.json", "sfep-analysis-config/v1"),
            "producer_runtime": ("producer_runtime.json", "sfep-producer-runtime/v1"),
            "equipment_operating_ranges": (
                "equipment_operating_ranges.json", "sfep-operating-ranges/v1",
            ),
            "quality_risk_intervals": (
                "quality_risk_intervals.json", "sfep-quality-rules/v1",
            ),
            "replay_events": ("replay_events.csv", "sfep-replay-events/v1"),
            "analysis_summary": ("analysis_summary.json", "sfep-analysis-summary/v1"),
        }
        assert {item["role"] for item in manifest["artifacts"]} == set(expected_artifacts)
        for item in manifest["artifacts"]:
            filename, schema_version = expected_artifacts[item["role"]]
            size, digest = run.snapshot[filename]
            assert item == {
                "role": item["role"],
                "schemaVersion": schema_version,
                "sha256": "sha256:" + digest,
                "sizeBytes": size,
            }
        for filename in run.snapshot:
            payload = run.payload(filename)
            assert all(marker not in payload for marker in run.volatile_markers)


def test_sealed_runtime_sites_remain_free_of_bytecode_caches(baseline_runs) -> None:
    assert baseline_runs.cache_before == ((), ())
    assert baseline_runs.cache_after == ((), ())


def test_holdout_values_change_replay_without_changing_learned_criteria(
    holdout_mutation,
) -> None:
    baseline = holdout_mutation.baseline
    mutated = holdout_mutation.mutated
    base_manifest = baseline.json_payloads["bundle_manifest.json"]
    changed_manifest = mutated.json_payloads["bundle_manifest.json"]
    _assert_manifest_sources_match_bytes(
        changed_manifest, holdout_mutation.mutation.data_dir
    )

    assert holdout_mutation.mutation.population == "HOLDOUT"
    assert holdout_mutation.mutation.changed_files == {
        "sts_2fur_hr_2.csv": ("f_heat_temp",),
        "sts_3ap_3.csv": ("judge",),
    }
    assert (
        holdout_mutation.mutation.original_hashes_before
        == holdout_mutation.mutation.original_hashes_after
    )
    assert changed_manifest["criteriaId"] == base_manifest["criteriaId"]
    assert changed_manifest["criteriaIdentity"] == base_manifest["criteriaIdentity"]
    assert (
        mutated.payload("equipment_operating_ranges.json")
        == baseline.payload("equipment_operating_ranges.json")
    )
    assert (
        mutated.payload("quality_risk_intervals.json")
        == baseline.payload("quality_risk_intervals.json")
    )
    assert mutated.bundle_id != baseline.bundle_id
    assert mutated.payload("replay_events.csv") != baseline.payload("replay_events.csv")
    assert (
        mutated.payload("analysis_summary.json")
        != baseline.payload("analysis_summary.json")
    )
    assert changed_manifest["identity"] != base_manifest["identity"]
    assert (
        changed_manifest["identity"]["source.sm_cc.sha256"]
        == base_manifest["identity"]["source.sm_cc.sha256"]
    )
    for role in ("fur_hr", "ap"):
        assert (
            changed_manifest["identity"][f"source.{role}.sha256"]
            != base_manifest["identity"][f"source.{role}.sha256"]
        )


def test_discovery_label_change_updates_the_mature_criteria_projection(
    reference_mutation,
) -> None:
    baseline = reference_mutation.baseline
    mutated = reference_mutation.mutated
    base_manifest = baseline.json_payloads["bundle_manifest.json"]
    changed_manifest = mutated.json_payloads["bundle_manifest.json"]
    _assert_manifest_sources_match_bytes(
        changed_manifest, reference_mutation.mutation.data_dir
    )

    assert reference_mutation.mutation.population == "DISCOVERY"
    assert reference_mutation.mutation.changed_files == {
        "sts_3ap_3.csv": ("judge",),
    }
    assert (
        reference_mutation.mutation.original_hashes_before
        == reference_mutation.mutation.original_hashes_after
    )
    assert (
        changed_manifest["criteriaIdentity"]["criteria_projection_sha256"]
        != base_manifest["criteriaIdentity"]["criteria_projection_sha256"]
    )
    assert changed_manifest["criteriaId"] != base_manifest["criteriaId"]
    assert mutated.bundle_id != baseline.bundle_id


def test_persistent_local_a_b_outputs_preserve_the_verified_bundle(
    persistent_runs,
) -> None:
    first, second = persistent_runs.runs
    assert first.output_root.name.startswith("run-a")
    assert second.output_root.name.startswith("run-b")
    assert first.bundle_id == second.bundle_id == persistent_runs.baseline.bundle_id
    assert first.snapshot == second.snapshot == persistent_runs.baseline.snapshot
    assert persistent_runs.summary_checkpoints[0] == persistent_runs.summary_checkpoints[1]
    checkpoint = persistent_runs.summary_checkpoints[0]
    assert set(checkpoint["splitCounts"]) == {
        "reference", "discovery", "confirmation", "holdout",
    }
    assert set(checkpoint["quarantineCounts"]) == {
        "DUPLICATE_AP_PROD_ID",
        "DUPLICATE_FUR_HR_COIL_KEY",
        "DUPLICATE_FUR_HR_KEY",
        "DUPLICATE_SM_CC_KEY",
        "MISSING_AP_PROD_ID",
        "UNLINKED_AP",
        "UNLINKED_FUR_HR",
        "UNLINKED_SM_CC",
    }
    assert set(checkpoint["labelCensoringCounts"]) == {
        "AP_UNLINKED", "LABEL_NOT_YET_AVAILABLE",
    }
    assert checkpoint["rangeCount"] > 0
    assert checkpoint["ruleCount"] > 0
    assert checkpoint["eventCount"] > 0
