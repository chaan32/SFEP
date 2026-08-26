from __future__ import annotations

import csv
import hashlib
import io
import importlib.resources
import json
import os
import shutil
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from jsonschema import ValidationError

from equipment_quality.genealogy import build_genealogy
from equipment_quality.schema import load_analysis_config, read_inputs
from equipment_quality.time_split import build_time_split
from factories.schema_time import (
    tables,
    tables_with_different_sm_ap_linkage_same_fur,
    tables_with_duplicate_ap,
    tables_with_known_record_numbers,
    tables_with_unlinked_sm_and_ap,
)


SM_HEADER = [
    "sm_plant", "charge_id", "steel_grade", "steel_usage", "delta_ferrite",
    "ingre_cr", "ingre_ni", "ingre_s", "cast_date", "cc_gubun",
    "tundish_temp", "mlac_ratio", "slab_no", "slab_gubun", "slab_grind",
]
FUR_HEADER = [
    "charge_id", "slab_no", "furnace_no", "f_jangip_gubun",
    "f_jangip_temp", "f_bfg", "f_cog", "f_ldg", "f_bfg_per", "f_cog_per",
    "f_ldg_per", "f_pre_temp", "f_heat_temp", "f_sock_temp",
    "f_pre_interval", "f_heat_interval", "f_sock_interval", "f_ext_date",
    "f_ext_time", "hr_coil_id", "hr_date", "hr_thick", "hr_width",
    "rm4_temp", "rm_pitch", "slab_width",
]
AP_HEADER = [
    "judge", "hr_coil_id", "ap_plant", "ap_prod_id", "ap_date", "ap_shift",
    "ap_thick", "ap_width", "ap_line_speed",
]

VALID_ROWS = {
    "sts_1sm_cc_1.csv": [
        "1공장", " C1 ", "S1", "RZ1", "69", "18.24", "8.63", "0.02",
        "2025-01-01", "2연주", "1485", "95.1", " 1 ", "C", "MISS",
    ],
    "sts_2fur_hr_2.csv": [
        " C1 ", " 1 ", "1호기", "CCR", "30", "25", "25", "50", "25",
        "25", "50", "1130", "1254", "1260", "89", "46", "48",
        "2025-01-02", "19", " H1 ", "2025-01-02", "3.04", "1041",
        "1113", "91", "1030",
    ],
    "sts_3ap_3.csv": [
        "불량", " H1 ", "1공장", " A1 ", "2025-01-03", "B", "3.4",
        "1258", "37.6",
    ],
}

HEADERS = {
    "sts_1sm_cc_1.csv": SM_HEADER,
    "sts_2fur_hr_2.csv": FUR_HEADER,
    "sts_3ap_3.csv": AP_HEADER,
}

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_ROOT = REPOSITORY_ROOT / "analysis"
NORMATIVE_CONFIG_SCHEMA = (
    REPOSITORY_ROOT
    / "contracts/equipment-monitor/v1/analysis_config.schema.json"
)


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode("cp949")


def _write_sources(
    root: Path,
    *,
    headers: dict[str, list[str]] | None = None,
    rows: dict[str, list[list[str]]] | None = None,
) -> dict[str, bytes]:
    root.mkdir()
    payloads: dict[str, bytes] = {}
    for name in HEADERS:
        payload = _csv_bytes(
            (headers or HEADERS)[name],
            (rows or {key: [value] for key, value in VALID_ROWS.items()})[name],
        )
        (root / name).write_bytes(payload)
        payloads[name] = payload
    return payloads


def _mutated_rows(name: str, column: str, value: str) -> dict[str, list[list[str]]]:
    rows = {key: [list(record)] for key, record in VALID_ROWS.items()}
    rows[name][0][HEADERS[name].index(column)] = value
    return rows


def test_read_inputs_decodes_cp949_trims_only_ids_and_records_source_bytes(tmp_path):
    data_dir = tmp_path / "원본"
    payloads = _write_sources(data_dir)

    inputs = read_inputs(data_dir)

    assert inputs.sm_cc.loc[0, "sm_plant"] == "1공장"
    assert inputs.sm_cc.loc[0, "charge_id"] == "C1"
    assert inputs.sm_cc.loc[0, "slab_no"] == "1"
    assert inputs.fur_hr.loc[0, "hr_coil_id"] == "H1"
    assert inputs.ap.loc[0, "ap_prod_id"] == "A1"
    assert inputs.fur_hr.loc[0, "hr_date"] == date(2025, 1, 2)
    assert inputs.fur_hr.loc[0, "f_bfg"] == 25.0
    assert [source.role for source in inputs.sources] == ["sm_cc", "fur_hr", "ap"]
    assert [source.name for source in inputs.sources] == [
        "sts_1sm_cc_1.csv", "sts_2fur_hr_2.csv", "sts_3ap_3.csv",
    ]
    for source in inputs.sources:
        assert not hasattr(source, "path")
        expected = payloads[source.name]
        assert source.size_bytes == len(expected)
        assert source.sha256 == "sha256:" + hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda header: header[:-1],
        lambda header: [*header, "unexpected"],
        lambda header: [header[1], header[0], *header[2:]],
    ],
    ids=["missing", "extra", "reordered"],
)
def test_read_inputs_rejects_non_exact_ordered_headers(tmp_path, mutation):
    headers = {name: list(header) for name, header in HEADERS.items()}
    headers["sts_2fur_hr_2.csv"] = mutation(headers["sts_2fur_hr_2.csv"])
    data_dir = tmp_path / "data"
    _write_sources(data_dir, headers=headers)

    with pytest.raises(ValueError, match="ordered header"):
        read_inputs(data_dir)


def test_read_inputs_requires_exact_csv_basenames(tmp_path):
    missing_dir = tmp_path / "missing"
    _write_sources(missing_dir)
    (missing_dir / "sts_3ap_3.csv").unlink()
    with pytest.raises(ValueError, match="filenames"):
        read_inputs(missing_dir)

    extra_dir = tmp_path / "extra"
    _write_sources(extra_dir)
    (extra_dir / "renamed-copy.csv").write_bytes(b"x\n")
    with pytest.raises(ValueError, match="filenames"):
        read_inputs(extra_dir)


def test_read_inputs_rejects_non_cp949_bytes(tmp_path):
    data_dir = tmp_path / "data"
    _write_sources(data_dir)
    (data_dir / "sts_1sm_cc_1.csv").write_bytes(b"\xff\xff\n")

    with pytest.raises(ValueError, match="CP949"):
        read_inputs(data_dir)


@pytest.mark.parametrize(
    ("name", "column"),
    [
        ("sts_1sm_cc_1.csv", "charge_id"),
        ("sts_1sm_cc_1.csv", "slab_no"),
        ("sts_2fur_hr_2.csv", "charge_id"),
        ("sts_2fur_hr_2.csv", "slab_no"),
        ("sts_2fur_hr_2.csv", "hr_coil_id"),
        ("sts_3ap_3.csv", "hr_coil_id"),
        ("sts_3ap_3.csv", "ap_prod_id"),
    ],
)
def test_read_inputs_rejects_identifier_empty_after_trim(tmp_path, name, column):
    data_dir = tmp_path / "data"
    _write_sources(data_dir, rows=_mutated_rows(name, column, " \t "))

    with pytest.raises(ValueError, match="non-empty identifier"):
        read_inputs(data_dir)


def test_read_inputs_preserves_empty_identifier_for_deterministic_quarantine(tmp_path):
    data_dir = tmp_path / "data"
    _write_sources(
        data_dir,
        rows=_mutated_rows("sts_3ap_3.csv", "ap_prod_id", ""),
    )

    inputs = read_inputs(data_dir)
    result = build_genealogy(inputs)

    assert pd.isna(inputs.ap.loc[0, "ap_prod_id"])
    assert "MISSING_AP_PROD_ID" in set(result.quarantine_rows["reason"])


@pytest.mark.parametrize(
    ("name", "column"),
    [
        ("sts_1sm_cc_1.csv", "cast_date"),
        ("sts_2fur_hr_2.csv", "f_ext_date"),
        ("sts_2fur_hr_2.csv", "hr_date"),
        ("sts_3ap_3.csv", "ap_date"),
    ],
)
def test_read_inputs_rejects_non_iso_or_impossible_dates(tmp_path, name, column):
    data_dir = tmp_path / "data"
    _write_sources(data_dir, rows=_mutated_rows(name, column, "2025-02-30"))

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        read_inputs(data_dir)


@pytest.mark.parametrize("judge", ["GOOD", "불 량", "양품 "])
def test_read_inputs_rejects_invalid_nonempty_judge(tmp_path, judge):
    data_dir = tmp_path / "data"
    _write_sources(
        data_dir,
        rows=_mutated_rows("sts_3ap_3.csv", "judge", judge),
    )

    with pytest.raises(ValueError, match="judge"):
        read_inputs(data_dir)


def test_read_inputs_allows_missing_judge_as_censored_label(tmp_path):
    data_dir = tmp_path / "data"
    _write_sources(
        data_dir,
        rows=_mutated_rows("sts_3ap_3.csv", "judge", ""),
    )

    inputs = read_inputs(data_dir)

    assert pd.isna(inputs.ap.loc[0, "judge"])


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "not-a-number"])
def test_read_inputs_rejects_non_finite_or_non_numeric_values(tmp_path, value):
    data_dir = tmp_path / "data"
    _write_sources(
        data_dir,
        rows=_mutated_rows("sts_2fur_hr_2.csv", "f_bfg", value),
    )

    with pytest.raises(ValueError, match="finite numeric"):
        read_inputs(data_dir)


@pytest.mark.parametrize("value", ["-1", "24", "1.5"])
def test_read_inputs_rejects_hour_outside_integer_zero_to_twenty_three(tmp_path, value):
    data_dir = tmp_path / "data"
    _write_sources(
        data_dir,
        rows=_mutated_rows("sts_2fur_hr_2.csv", "f_ext_time", value),
    )

    with pytest.raises(ValueError, match="hour"):
        read_inputs(data_dir)


def test_source_record_number_counts_csv_records_not_multiline_physical_lines(tmp_path):
    rows = {key: [list(value)] for key, value in VALID_ROWS.items()}
    first_sm = rows["sts_1sm_cc_1.csv"][0]
    first_sm[SM_HEADER.index("steel_usage")] = "RZ1\nRZ2"
    second_sm = list(first_sm)
    second_sm[SM_HEADER.index("charge_id")] = "C2"
    second_sm[SM_HEADER.index("slab_no")] = "2"
    rows["sts_1sm_cc_1.csv"].append(second_sm)
    data_dir = tmp_path / "data"
    _write_sources(data_dir, rows=rows)

    inputs = read_inputs(data_dir)

    assert inputs.sm_cc["_source_record_number"].tolist() == [2, 3]
    assert inputs.sm_cc.loc[0, "steel_usage"] == "RZ1\nRZ2"


def test_load_analysis_config_is_complete_validated_and_deeply_immutable():
    config = load_analysis_config(Path("analysis/analysis_config.json"))

    assert config.schema_version == "sfep-analysis-config/v1"
    assert config.analysis_config_version == "quality-analysis-v1"
    assert config.reference_fraction == 0.70
    assert config.discovery_fraction == 0.70
    assert config.label_maturity_days == 38
    assert len(config.fields) == 47
    assert len(config.range_context_hierarchies) == 4
    assert len(config.risk_adjustment_hierarchies) == 4
    assert config.fixed_interactions[-1] == ("ap_line_speed", "ap_thick")
    assert config.fdr_families == ("NUMERIC", "CATEGORICAL", "INTERACTION")
    derived = next(field for field in config.fields if field["field"] == "f_bfg_ratio")
    assert derived["sourceRole"] is None
    assert derived["sourceColumn"] is None
    assert derived["dependencies"] == ("f_bfg", "f_cog", "f_ldg")
    with pytest.raises(TypeError):
        config.quality_risk["minimumDiscoverySupport"] = 1
    with pytest.raises(TypeError):
        derived["field"] = "changed"
    with pytest.raises(FrozenInstanceError):
        config.label_maturity_days = 1


def test_packaged_analysis_config_schema_bytes_match_normative_contract():
    packaged_schema = importlib.resources.files("equipment_quality").joinpath(
        "analysis_config.schema.json"
    )

    assert packaged_schema.is_file()
    assert packaged_schema.read_bytes() == NORMATIVE_CONFIG_SCHEMA.read_bytes()


def test_installed_wheel_loads_config_outside_checkout_without_contracts(tmp_path):
    wheel_source = tmp_path / "wheel-source"
    wheel_source.mkdir()
    shutil.copy2(ANALYSIS_ROOT / "pyproject.toml", wheel_source / "pyproject.toml")
    shutil.copytree(
        ANALYSIS_ROOT / "equipment_quality",
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
    config_path = outside_checkout / "analysis_config.json"
    config_path.write_bytes((ANALYSIS_ROOT / "analysis_config.json").read_bytes())
    assert not (tmp_path / "contracts").exists()
    assert not (outside_checkout / "contracts").exists()

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(installed_root)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["SFEP_INSTALLED_ROOT"] = str(installed_root)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os\n"
                "import hashlib\n"
                "import importlib.resources\n"
                "from pathlib import Path\n"
                "import equipment_quality\n"
                "from equipment_quality.schema import load_analysis_config\n"
                "root = Path(os.environ['SFEP_INSTALLED_ROOT']).resolve()\n"
                "module = Path(equipment_quality.__file__).resolve()\n"
                "assert module.is_relative_to(root), module\n"
                "assert not (Path.cwd() / 'contracts').exists()\n"
                "schema = importlib.resources.files('equipment_quality').joinpath('analysis_config.schema.json').read_bytes()\n"
                "print(hashlib.sha256(schema).hexdigest())\n"
                "config = load_analysis_config(Path('analysis_config.json'))\n"
                "print(config.analysis_config_version)\n"
            ),
        ],
        cwd=outside_checkout,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert probe.stdout.splitlines() == [
        hashlib.sha256(NORMATIVE_CONFIG_SCHEMA.read_bytes()).hexdigest(),
        "quality-analysis-v1",
    ]


@pytest.mark.parametrize("mutation", ["missing", "unknown", "nested_unknown"])
def test_load_analysis_config_rejects_missing_and_unknown_keys_without_defaults(
    tmp_path, mutation
):
    payload = json.loads(Path("analysis/analysis_config.json").read_text(encoding="utf-8"))
    if mutation == "missing":
        del payload["labelMaturityDays"]
    elif mutation == "unknown":
        payload["defaultMaturity"] = 38
    else:
        payload["splits"]["fallbackFraction"] = 0.70
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_analysis_config(path)


def test_load_analysis_config_rejects_duplicate_top_level_json_member(tmp_path):
    text = (ANALYSIS_ROOT / "analysis_config.json").read_text(encoding="utf-8")
    path = tmp_path / "duplicate-top-level.json"
    path.write_text(
        text.replace("{", '{"schemaVersion":"not-a-schema",', 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        load_analysis_config(path)

    assert str(error.value) == (
        "analysis config JSON contains duplicate object member name: schemaVersion"
    )


def test_load_analysis_config_rejects_duplicate_nested_json_member(tmp_path):
    text = (ANALYSIS_ROOT / "analysis_config.json").read_text(encoding="utf-8")
    path = tmp_path / "duplicate-nested.json"
    path.write_text(
        text.replace(
            '"splits":{',
            '"splits":{"referenceFraction":0.1,',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        load_analysis_config(path)

    assert str(error.value) == (
        "analysis config JSON contains duplicate object member name: referenceFraction"
    )


@pytest.mark.parametrize(
    "hierarchy_name",
    ["rangeContextHierarchies", "riskAdjustmentHierarchies"],
)
def test_load_analysis_config_rejects_duplicate_equipment_type_json_member(
    tmp_path, hierarchy_name
):
    text = (ANALYSIS_ROOT / "analysis_config.json").read_text(encoding="utf-8")
    marker = f'"{hierarchy_name}":['
    before, separator, hierarchy_and_rest = text.partition(marker)
    assert separator == marker
    hierarchy_and_rest = hierarchy_and_rest.replace(
        '{"equipmentType":"SM_CC",',
        '{"equipmentType":"NOT_A_REAL_TYPE","equipmentType":"SM_CC",',
        1,
    )
    path = tmp_path / f"duplicate-{hierarchy_name}.json"
    path.write_text(before + separator + hierarchy_and_rest, encoding="utf-8")

    with pytest.raises(ValueError) as error:
        load_analysis_config(path)

    assert str(error.value) == (
        "analysis config JSON contains duplicate object member name: equipmentType"
    )


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_load_analysis_config_rejects_non_standard_numeric_tokens_at_parse_time(
    tmp_path, token
):
    text = Path("analysis/analysis_config.json").read_text(encoding="utf-8")
    path = tmp_path / "config.json"
    path.write_text(text.replace("1.959963984540054", token), encoding="utf-8")

    with pytest.raises(ValueError) as error:
        load_analysis_config(path)

    assert str(error.value) == (
        f"analysis config JSON contains non-standard numeric token: {token}"
    )


@pytest.mark.parametrize(
    "hierarchy_name",
    ["rangeContextHierarchies", "riskAdjustmentHierarchies"],
)
def test_load_analysis_config_rejects_duplicate_hierarchy_equipment_policy(
    tmp_path, hierarchy_name
):
    payload = json.loads((ANALYSIS_ROOT / "analysis_config.json").read_text("utf-8"))
    payload[hierarchy_name][-1]["equipmentType"] = "SM_CC"
    path = tmp_path / "duplicate-hierarchy.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one policy for each equipment type"):
        load_analysis_config(path)


@pytest.mark.parametrize(
    ("hierarchy_name", "mutation"),
    [
        ("rangeContextHierarchies", "missing"),
        ("rangeContextHierarchies", "extra"),
        ("riskAdjustmentHierarchies", "missing"),
        ("riskAdjustmentHierarchies", "extra"),
    ],
)
def test_load_analysis_config_rejects_missing_or_extra_hierarchy_policy(
    tmp_path, hierarchy_name, mutation
):
    payload = json.loads((ANALYSIS_ROOT / "analysis_config.json").read_text("utf-8"))
    if mutation == "missing":
        payload[hierarchy_name].pop()
    else:
        payload[hierarchy_name].append(dict(payload[hierarchy_name][-1]))
    path = tmp_path / f"{mutation}-hierarchy.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_analysis_config(path)


def test_build_genealogy_never_joins_charge_without_slab():
    inputs = tables(
        sm=[
            {"charge_id": "C1", "slab_no": "1"},
            {"charge_id": "C1", "slab_no": "2"},
        ],
        fur=[{"charge_id": "C1", "slab_no": "2", "hr_coil_id": "H2"}],
        ap=[{"hr_coil_id": "H2", "judge": "불량"}],
    )

    result = build_genealogy(inputs)

    assert result.replay_rows.iloc[0]["slab_no"] == "2"
    assert len(result.replay_rows) == 1


def test_ambiguous_duplicate_ap_is_quarantined_not_first_wins():
    result = build_genealogy(tables_with_duplicate_ap("H2"))

    assert result.quality_rows.empty
    assert set(result.quarantine_rows["reason"]) == {"DUPLICATE_AP_KEY"}
    assert len(result.quarantine_rows) == 2
    assert len(result.replay_rows) == 1
    assert result.replay_rows.iloc[0]["label_status"] == "AP_UNLINKED"


def test_boundary_population_is_fur_only_and_ignores_sm_ap_linkage():
    baseline = build_genealogy(tables_with_unlinked_sm_and_ap())
    mutated = build_genealogy(tables_with_different_sm_ap_linkage_same_fur())

    pd.testing.assert_frame_equal(baseline.boundary_rows, mutated.boundary_rows)


def test_boundary_requires_only_unique_composite_key_and_valid_hr_date():
    inputs = tables(
        sm=[],
        fur=[{"charge_id": "C1", "slab_no": "1", "hr_coil_id": None}],
        ap=[],
    )

    result = build_genealogy(inputs)

    assert result.boundary_rows[["charge_id", "slab_no"]].to_dict("records") == [
        {"charge_id": "C1", "slab_no": "1"}
    ]
    assert result.replay_rows.empty
    assert "MISSING_FUR_HR_COIL_ID" in set(result.quarantine_rows["reason"])


@pytest.mark.parametrize(
    ("inputs", "reason"),
    [
        (
            lambda: tables(
                sm=[{"charge_id": "C1", "slab_no": "1"}, {"charge_id": "C1", "slab_no": "1"}],
            ),
            "DUPLICATE_SM_CC_KEY",
        ),
        (
            lambda: tables(
                fur=[{"charge_id": "C1", "slab_no": "1"}, {"charge_id": "C1", "slab_no": "1"}],
            ),
            "DUPLICATE_FUR_HR_KEY",
        ),
        (
            lambda: tables_with_duplicate_ap("H1"),
            "DUPLICATE_AP_KEY",
        ),
        (
            lambda: tables(
                sm=[{"charge_id": "C1", "slab_no": "1"}, {"charge_id": "C2", "slab_no": "2"}],
                fur=[
                    {"charge_id": "C1", "slab_no": "1", "hr_coil_id": "H1"},
                    {"charge_id": "C2", "slab_no": "2", "hr_coil_id": "H1"},
                ],
                ap=[],
            ),
            "DUPLICATE_FUR_HR_COIL_KEY",
        ),
    ],
)
def test_every_ambiguous_key_quarantines_all_rows_without_selecting_one(inputs, reason):
    result = build_genealogy(inputs())

    matching = result.quarantine_rows[result.quarantine_rows["reason"] == reason]
    assert len(matching) == 2


def test_overlapping_fur_duplicate_keys_use_primary_composite_reason_once():
    result = build_genealogy(
        tables(
            sm=[],
            fur=[
                {
                    "charge_id": "C-DUP",
                    "slab_no": "1",
                    "hr_coil_id": "H-DUP",
                    "_source_record_number": 41,
                },
                {
                    "charge_id": "C-DUP",
                    "slab_no": "1",
                    "hr_coil_id": "H-DUP",
                    "_source_record_number": 42,
                },
            ],
            ap=[],
        )
    )

    assert list(
        result.quarantine_rows[
            ["source_role", "source_record_number", "reason"]
        ].itertuples(index=False, name=None)
    ) == [
        ("fur_hr", 41, "DUPLICATE_FUR_HR_KEY"),
        ("fur_hr", 42, "DUPLICATE_FUR_HR_KEY"),
    ]


def test_overlapping_ap_duplicate_keys_use_primary_join_reason_once():
    result = build_genealogy(
        tables(
            sm=[],
            fur=[],
            ap=[
                {
                    "hr_coil_id": "H-DUP",
                    "ap_prod_id": "A-DUP",
                    "_source_record_number": 61,
                },
                {
                    "hr_coil_id": "H-DUP",
                    "ap_prod_id": "A-DUP",
                    "_source_record_number": 62,
                },
            ],
        )
    )

    assert list(
        result.quarantine_rows[
            ["source_role", "source_record_number", "reason"]
        ].itertuples(index=False, name=None)
    ) == [
        ("ap", 61, "DUPLICATE_AP_KEY"),
        ("ap", 62, "DUPLICATE_AP_KEY"),
    ]


@pytest.mark.parametrize(
    ("role", "inputs", "reason"),
    [
        ("sm_cc", lambda: tables(sm=[{"charge_id": None}]), "MISSING_SM_CC_KEY"),
        ("fur_hr", lambda: tables(fur=[{"slab_no": ""}]), "MISSING_FUR_HR_KEY"),
        ("fur_hr", lambda: tables(fur=[{"hr_coil_id": None}]), "MISSING_FUR_HR_COIL_ID"),
        ("ap", lambda: tables(ap=[{"hr_coil_id": None}]), "MISSING_AP_HR_COIL_ID"),
        ("ap", lambda: tables(ap=[{"ap_prod_id": None}]), "MISSING_AP_PROD_ID"),
    ],
)
def test_missing_required_ids_are_quarantined_with_source_specific_reason(
    role, inputs, reason
):
    result = build_genealogy(inputs())

    matching = result.quarantine_rows[
        (result.quarantine_rows["source_role"] == role)
        & (result.quarantine_rows["reason"] == reason)
    ]
    assert len(matching) == 1


def test_overlapping_missing_ids_use_primary_genealogy_reason_once():
    result = build_genealogy(
        tables(
            sm=[
                {
                    "charge_id": None,
                    "slab_no": None,
                    "_source_record_number": 71,
                }
            ],
            fur=[
                {
                    "charge_id": None,
                    "slab_no": None,
                    "hr_coil_id": None,
                    "_source_record_number": 72,
                }
            ],
            ap=[
                {
                    "hr_coil_id": None,
                    "ap_prod_id": None,
                    "_source_record_number": 73,
                }
            ],
        )
    )

    assert list(
        result.quarantine_rows[
            ["source_role", "source_record_number", "reason"]
        ].itertuples(index=False, name=None)
    ) == [
        ("sm_cc", 71, "MISSING_SM_CC_KEY"),
        ("fur_hr", 72, "MISSING_FUR_HR_KEY"),
        ("ap", 73, "MISSING_AP_HR_COIL_ID"),
    ]


@pytest.mark.parametrize(
    "input_factory",
    [
        pytest.param(
            lambda: tables(
                sm=[
                    {"charge_id": "C1", "slab_no": "1"},
                    {"charge_id": "C1", "slab_no": "1"},
                ],
                fur=[],
                ap=[],
            ),
            id="sm-duplicate",
        ),
        pytest.param(
            lambda: tables(
                sm=[],
                fur=[
                    {"charge_id": "C1", "slab_no": "1", "hr_coil_id": "H1"},
                    {"charge_id": "C1", "slab_no": "1", "hr_coil_id": "H1"},
                ],
                ap=[],
            ),
            id="fur-overlapping-duplicates",
        ),
        pytest.param(
            lambda: tables(
                sm=[],
                fur=[],
                ap=[
                    {"hr_coil_id": "H1", "ap_prod_id": "A1"},
                    {"hr_coil_id": "H1", "ap_prod_id": "A1"},
                ],
            ),
            id="ap-overlapping-duplicates",
        ),
        pytest.param(
            lambda: tables(
                sm=[{"charge_id": None, "slab_no": None}],
                fur=[{"charge_id": None, "slab_no": None, "hr_coil_id": None}],
                ap=[{"hr_coil_id": None, "ap_prod_id": None}],
            ),
            id="overlapping-missing-ids",
        ),
        pytest.param(
            lambda: tables(
                sm=[{"cast_date": "2025-01-03"}],
                fur=[{"f_ext_date": "2025-01-02", "hr_date": "2025-01-04"}],
                ap=[{"ap_date": "2025-01-05"}],
            ),
            id="impossible-date-linked-chain",
        ),
        pytest.param(tables_with_unlinked_sm_and_ap, id="unlinked-branches"),
    ],
)
def test_every_quarantine_source_record_pair_is_unique_for_adversarial_branches(
    input_factory,
):
    result = build_genealogy(input_factory())
    pairs = list(
        result.quarantine_rows[
            ["source_role", "source_record_number"]
        ].itertuples(index=False, name=None)
    )

    assert len(pairs) == len(set(pairs))


def test_unlinked_fur_is_not_replayed_and_unlinked_source_rows_are_audited():
    result = build_genealogy(tables_with_unlinked_sm_and_ap())

    assert result.replay_rows.empty
    assert set(result.quarantine_rows["reason"]) == {
        "UNLINKED_SM_CC", "UNLINKED_FUR_HR", "UNLINKED_AP"
    }


def test_ap_unlinked_material_stays_replayable_and_censored():
    result = build_genealogy(tables(ap=[]))

    assert len(result.replay_rows) == 1
    assert result.quality_rows.empty
    assert result.replay_rows.iloc[0]["label_status"] == "AP_UNLINKED"
    assert pd.isna(result.replay_rows.iloc[0]["ap_record_number"])
    assert result.audit["label_censoring"] == {"AP_UNLINKED": 1}


@pytest.mark.parametrize(
    ("sm_date", "ext_date", "hr_date", "ap_date"),
    [
        ("2025-01-03", "2025-01-02", "2025-01-04", "2025-01-05"),
        ("2025-01-01", "2025-01-04", "2025-01-03", "2025-01-05"),
        ("2025-01-01", "2025-01-02", "2025-01-04", "2025-01-03"),
    ],
)
def test_impossible_stage_date_order_is_quarantined_not_replayed(
    sm_date, ext_date, hr_date, ap_date
):
    result = build_genealogy(
        tables(
            sm=[{"cast_date": sm_date}],
            fur=[{"f_ext_date": ext_date, "hr_date": hr_date}],
            ap=[{"ap_date": ap_date}],
        )
    )

    assert result.replay_rows.empty
    assert set(result.quarantine_rows["reason"]) == {"IMPOSSIBLE_STAGE_DATE_ORDER"}


def test_impossible_stage_date_order_attributes_every_linked_source_record_once():
    result = build_genealogy(
        tables(
            sm=[
                {
                    "_source_record_number": 17,
                    "cast_date": "2025-01-03",
                }
            ],
            fur=[
                {
                    "_source_record_number": 23,
                    "f_ext_date": "2025-01-02",
                    "hr_date": "2025-01-04",
                }
            ],
            ap=[
                {
                    "_source_record_number": 31,
                    "ap_date": "2025-01-05",
                }
            ],
        )
    )

    rejected = result.quarantine_rows.loc[
        result.quarantine_rows["reason"] == "IMPOSSIBLE_STAGE_DATE_ORDER",
        ["source_role", "source_name", "source_record_number"],
    ]
    assert list(rejected.itertuples(index=False, name=None)) == [
        ("sm_cc", "sts_1sm_cc_1.csv", 17),
        ("fur_hr", "sts_2fur_hr_2.csv", 23),
        ("ap", "sts_3ap_3.csv", 31),
    ]
    assert result.audit["quarantine"]["IMPOSSIBLE_STAGE_DATE_ORDER"] == 3


def test_join_preserves_exact_source_record_provenance_without_absolute_paths():
    result = build_genealogy(tables_with_known_record_numbers())

    lineage = result.lineage_rows.iloc[0]
    assert lineage["sm_cc_record_number"] == 2
    assert lineage["fur_hr_record_number"] == 4
    assert lineage["ap_record_number"] == 3
    assert [(record.role, record.name, record.record_number) for record in lineage["source_records"]] == [
        ("sm_cc", "sts_1sm_cc_1.csv", 2),
        ("fur_hr", "sts_2fur_hr_2.csv", 4),
        ("ap", "sts_3ap_3.csv", 3),
    ]
    assert all(not Path(record.name).is_absolute() for record in lineage["source_records"])


def test_gas_ratios_are_derived_from_amounts_not_source_percent_columns():
    result = build_genealogy(tables())
    row = result.replay_rows.iloc[0]

    assert (
        row["f_bfg_ratio"],
        row["f_cog_ratio"],
        row["f_ldg_ratio"],
    ) == pytest.approx((0.25, 0.25, 0.5))
    assert row["f_bfg_per"] == 99.0
    assert "_source_record_number" not in result.replay_rows.columns
    assert not any(
        dependency.endswith("._source_record_number")
        for dependency in result.audit["derived_features"]["f_bfg_ratio"]
    )


@pytest.mark.parametrize(
    ("amounts", "expected"),
    [
        ((100.0, 30.0, 20.0), (0.6666666666666666, 0.2, 0.13333333333333333)),
        ((100.0, 0.0, 50.0), (0.6666666666666666, 0.0, 0.3333333333333333)),
    ],
    ids=["golden-fractions", "partial-zero-component"],
)
def test_gas_ratios_are_unit_fractions_for_nonzero_observed_amounts(
    amounts, expected
):
    result = build_genealogy(
        tables(
            fur=[
                {
                    "f_bfg": amounts[0],
                    "f_cog": amounts[1],
                    "f_ldg": amounts[2],
                }
            ]
        )
    )
    row = result.replay_rows.iloc[0]

    assert (
        row["f_bfg_ratio"],
        row["f_cog_ratio"],
        row["f_ldg_ratio"],
    ) == pytest.approx(expected)


@pytest.mark.parametrize(
    "amounts",
    [(0.0, 0.0, 0.0), (100.0, None, 50.0)],
    ids=["zero-total", "missing-component"],
)
def test_gas_ratios_are_missing_when_fraction_is_not_defined(amounts):
    result = build_genealogy(
        tables(
            fur=[
                {
                    "f_bfg": amounts[0],
                    "f_cog": amounts[1],
                    "f_ldg": amounts[2],
                }
            ]
        )
    )
    row = result.replay_rows.iloc[0]

    assert pd.isna(row["f_bfg_ratio"])
    assert pd.isna(row["f_cog_ratio"])
    assert pd.isna(row["f_ldg_ratio"])


def test_fixed_golden_source_runs_complete_task3_pipeline():
    golden_source = REPOSITORY_ROOT / "contracts/equipment-monitor/v1/golden-source"

    inputs = read_inputs(golden_source)
    genealogy = build_genealogy(inputs)
    split = build_time_split(
        genealogy,
        load_analysis_config(ANALYSIS_ROOT / "analysis_config.json"),
    )

    assert len(genealogy.boundary_rows) == 12
    assert len(genealogy.replay_rows) == 12
    assert len(genealogy.quality_rows) == 11
    assert len(genealogy.quarantine_rows) == 3
    h001 = genealogy.replay_rows.set_index("hr_coil_id").loc["H001"]
    assert (
        h001["f_bfg_ratio"],
        h001["f_cog_ratio"],
        h001["f_ldg_ratio"],
    ) == pytest.approx((0.6666666666666666, 0.2, 0.13333333333333333))
    assert split.as_of == date(2025, 2, 20)
    assert split.discovery_cutoff == date(2025, 1, 3)
    assert split.counts["discovery"]["total"] == 5
    assert split.counts["confirmation"]["total"] == 2


def test_quarantine_rows_have_stable_role_record_reason_order():
    result = build_genealogy(
        tables(
            sm=[{"charge_id": None}, {"charge_id": "C9", "slab_no": "9"}],
            fur=[{"charge_id": "C1", "slab_no": "1", "hr_coil_id": None}],
            ap=[{"hr_coil_id": "NO_FUR", "ap_prod_id": "A9"}],
        )
    )

    actual = list(
        result.quarantine_rows[
            ["source_role", "source_record_number", "reason"]
        ].itertuples(index=False, name=None)
    )
    role_rank = {"sm_cc": 0, "fur_hr": 1, "ap": 2}
    assert actual == sorted(actual, key=lambda row: (role_rank[row[0]], row[1], row[2]))


@pytest.mark.real_data
def test_approved_real_snapshot_quarantine_uses_51_unique_source_records():
    data_dir_value = os.environ.get("SFEP_STEEL_DATA_DIR")
    if data_dir_value is None:
        pytest.skip("SFEP_STEEL_DATA_DIR is required for the approved real snapshot")

    inputs = read_inputs(Path(data_dir_value))
    assert tuple(source.sha256 for source in inputs.sources) == (
        "sha256:0cd3e91428c005dae1785b9af01d30e3d08230e2058c68693c9aa7ffee043075",
        "sha256:c2bb0b503ec30b0e01e00d2bd88fde536479de59aebf1eae84131380d58f3bcf",
        "sha256:ff4572a1302459787ddca7458857864182d969e45b6a81edc4241bc47549801d",
    )
    result = build_genealogy(inputs)
    pairs = list(
        result.quarantine_rows[
            ["source_role", "source_record_number"]
        ].itertuples(index=False, name=None)
    )

    assert len(result.quarantine_rows) == 51
    assert len(pairs) == len(set(pairs)) == 51
    assert sum(result.audit["quarantine"].values()) == 51
