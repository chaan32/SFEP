"""Frozen data contracts shared by the offline producer stages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path, PurePath
from types import MappingProxyType
import pandas as pd


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _validate_sha256_uri(value: object, label: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a built-in string")
    if (
        len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must use the lowercase sha256 URI form")
    return value


def _mapping_items(value: object, label: str) -> list[tuple[str, object]]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    try:
        raw_items = list(value.items())
    except (AttributeError, TypeError, ValueError) as error:
        raise TypeError(f"{label} must expose a stable item sequence") from error
    items: list[tuple[str, object]] = []
    seen: set[str] = set()
    for key, item in raw_items:
        if type(key) is not str:
            raise TypeError(f"{label} keys must be built-in strings")
        if key in seen:
            raise ValueError(f"{label} keys must be unique")
        seen.add(key)
        items.append((key, item))
    return sorted(items, key=lambda pair: pair[0].encode("utf-8"))


def _freeze_json(value: object, label: str = "record") -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                key: _freeze_json(item, f"{label}.{key}")
                for key, item in _mapping_items(value, label)
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, f"{label}[]") for item in value
        )
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TypeError(f"{label} must contain only finite built-in JSON values")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _string_tuple(
    values: object,
    label: str,
    *,
    allow_empty_value: bool = False,
    require_utf8_sorted: bool = False,
) -> tuple[str, ...]:
    if type(values) is str:
        raise TypeError(f"{label} must be an iterable of strings")
    try:
        snapshot = tuple(values)  # type: ignore[arg-type]
    except TypeError as error:
        raise TypeError(f"{label} must be an iterable of strings") from error
    if any(
        type(value) is not str or (not allow_empty_value and not value)
        for value in snapshot
    ):
        raise ValueError(f"{label} must contain built-in non-empty strings")
    if len(snapshot) != len(set(snapshot)):
        raise ValueError(f"{label} must be unique")
    if require_utf8_sorted and snapshot != tuple(
        sorted(snapshot, key=lambda value: value.encode("utf-8"))
    ):
        raise ValueError(f"{label} must be UTF-8 sorted")
    return snapshot


@dataclass(frozen=True)
class Identity:
    value: str
    version: str
    fields: Mapping[str, str]

    def __post_init__(self) -> None:
        from equipment_quality.deterministic import id_lines, sha256_uri

        _validate_sha256_uri(self.value, "identity value")
        if type(self.version) is not str or self.version not in {
            "sfep-criteria-id/v1",
            "sfep-bundle-id/v1",
        }:
            raise ValueError("identity version must be a supported built-in string")
        items = _mapping_items(self.fields, "identity fields")
        fields: dict[str, str] = {}
        for key, value in items:
            if type(value) is not str:
                raise TypeError("identity field values must be built-in strings")
            if any(character in key or character in value for character in ("=", "\r", "\n")):
                raise ValueError("identity fields must be valid sfep-id-lines values")
            fields[key] = value
        criteria_keys = {
            "analysis_config_sha256",
            "as_of",
            "criteria_projection_sha256",
            "producer_runtime_sha256",
            "schema.analysis_config.sha256",
            "schema.equipment_operating_ranges.sha256",
            "schema.producer_runtime.sha256",
            "schema.quality_risk_intervals.sha256",
        }
        bundle_keys = {
            "analysis_config_sha256",
            "criteria_id",
            "producer_runtime_sha256",
            "schema.analysis_config.sha256",
            "schema.analysis_summary.sha256",
            "schema.bundle_manifest.sha256",
            "schema.equipment_operating_ranges.sha256",
            "schema.producer_runtime.sha256",
            "schema.quality_risk_intervals.sha256",
            "schema.replay_events.sha256",
            "source.ap.name",
            "source.ap.sha256",
            "source.ap.size_bytes",
            "source.fur_hr.name",
            "source.fur_hr.sha256",
            "source.fur_hr.size_bytes",
            "source.sm_cc.name",
            "source.sm_cc.sha256",
            "source.sm_cc.size_bytes",
        }
        expected_keys = (
            criteria_keys if self.version == "sfep-criteria-id/v1" else bundle_keys
        )
        if set(fields) != expected_keys:
            raise ValueError("identity fields do not match the version contract")
        for key, field_value in fields.items():
            if key.endswith("_sha256") or key.endswith(".sha256") or key == "criteria_id":
                _validate_sha256_uri(field_value, f"identity field {key}")
        if self.version == "sfep-criteria-id/v1":
            try:
                parsed_as_of = date.fromisoformat(fields["as_of"])
            except ValueError as error:
                raise ValueError("identity as_of must be an exact date") from error
            if parsed_as_of.isoformat() != fields["as_of"]:
                raise ValueError("identity as_of must be an exact date")
        else:
            fixed_names = {
                "source.sm_cc.name": "sts_1sm_cc_1.csv",
                "source.fur_hr.name": "sts_2fur_hr_2.csv",
                "source.ap.name": "sts_3ap_3.csv",
            }
            if any(fields[key] != name for key, name in fixed_names.items()):
                raise ValueError("identity source names must use fixed role/name triples")
            for role in ("sm_cc", "fur_hr", "ap"):
                size = fields[f"source.{role}.size_bytes"]
                if not size.isascii() or not size.isdecimal() or (
                    len(size) > 1 and size.startswith("0")
                ):
                    raise ValueError("identity source sizes must be canonical decimals")
        if self.value != sha256_uri(id_lines(self.version, fields)):
            raise ValueError("identity value does not match its fields")
        object.__setattr__(self, "fields", MappingProxyType(fields))


@dataclass(frozen=True)
class SourceFile:
    role: str
    name: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if self.role not in {"sm_cc", "fur_hr", "ap"}:
            raise ValueError(f"unknown source role: {self.role}")
        if PurePath(self.name).name != self.name or PurePath(self.name).is_absolute():
            raise ValueError("source name must be a basename")
        if self.size_bytes < 0:
            raise ValueError("source size must be non-negative")
        if not (
            self.sha256.startswith("sha256:")
            and len(self.sha256) == 71
            and all(character in "0123456789abcdef" for character in self.sha256[7:])
        ):
            raise ValueError("source sha256 must use the lowercase sha256 URI form")


@dataclass(frozen=True)
class SourceRecordRef:
    role: str
    name: str
    record_number: int

    def __post_init__(self) -> None:
        if self.role not in {"sm_cc", "fur_hr", "ap"}:
            raise ValueError(f"unknown source role: {self.role}")
        if PurePath(self.name).name != self.name or PurePath(self.name).is_absolute():
            raise ValueError("source record name must be a basename")
        if self.record_number < 2:
            raise ValueError("source record number must follow the header record")


@dataclass(frozen=True)
class FieldLineage:
    artifact_role: str
    output_field: str
    source_role: str | None
    source_column: str | None
    conversion: str
    dependencies: tuple[str, ...]
    first_available_stage: str | None


@dataclass(frozen=True)
class MaterialLineage:
    material_key: str
    charge_id: str
    slab_no: str
    hr_coil_id: str | None
    source_records: tuple[SourceRecordRef, ...]

    def __post_init__(self) -> None:
        _validate_sha256_uri(self.material_key, "material_key")
        for label, value in (("charge_id", self.charge_id), ("slab_no", self.slab_no)):
            if type(value) is not str or not value:
                raise ValueError(f"{label} must be a built-in non-empty string")
        if self.hr_coil_id is not None and (
            type(self.hr_coil_id) is not str or not self.hr_coil_id
        ):
            raise ValueError("hr_coil_id must be null or a built-in non-empty string")
        try:
            source_records = tuple(self.source_records)
        except TypeError as error:
            raise TypeError("source_records must be an iterable") from error
        if any(type(record) is not SourceRecordRef for record in source_records):
            raise TypeError("source_records must contain SourceRecordRef values")
        object.__setattr__(self, "source_records", source_records)


class _MaterialCatalogIndex:
    """One immutable, dual-indexed material catalog snapshot for a rich call."""

    def __init__(self, catalog: Sequence[MaterialLineage]) -> None:
        if type(catalog) is str:
            raise TypeError("material catalog must be a sequence")
        try:
            snapshot = tuple(catalog)
        except TypeError as error:
            raise TypeError("material catalog must be a sequence") from error
        by_key: dict[str, MaterialLineage] = {}
        by_pair: dict[tuple[str, str], MaterialLineage] = {}
        by_coil: dict[str, MaterialLineage] = {}
        for material in snapshot:
            if type(material) is not MaterialLineage:
                raise TypeError("material catalog must contain MaterialLineage values")
            if material.material_key in by_key:
                raise ValueError("material catalog has a duplicate material key")
            pair = (material.charge_id, material.slab_no)
            if pair in by_pair:
                raise ValueError("material catalog has a duplicate charge/slab pair")
            if material.hr_coil_id is not None and material.hr_coil_id in by_coil:
                raise ValueError("material catalog has a duplicate non-null hr_coil_id")
            by_key[material.material_key] = material
            by_pair[pair] = material
            if material.hr_coil_id is not None:
                by_coil[material.hr_coil_id] = material
        self._snapshot = snapshot
        self._by_pair = MappingProxyType(by_pair)
        self._by_coil = MappingProxyType(by_coil)

    def resolve_rows(self, rows: pd.DataFrame, label: str) -> tuple[str, ...]:
        if not isinstance(rows, pd.DataFrame):
            raise TypeError(f"{label} rows must be a pandas DataFrame")
        if not rows.columns.is_unique:
            raise ValueError(f"{label} rows must have unique columns")
        identity_columns = ("charge_id", "slab_no", "hr_coil_id")
        if any(column not in rows.columns for column in identity_columns):
            raise ValueError(f"{label} rows are missing required identity columns")
        columns = {column: rows[column].tolist() for column in identity_columns}
        resolved: list[str] = []
        seen: set[str] = set()
        for index in range(len(rows)):
            charge_id = columns["charge_id"][index]
            slab_no = columns["slab_no"][index]
            hr_coil_id = columns["hr_coil_id"][index]
            if any(
                type(value) is not str or not value
                for value in (charge_id, slab_no, hr_coil_id)
            ):
                raise ValueError(
                    f"{label} row identities must be built-in non-empty strings"
                )
            pair_material = self._by_pair.get((charge_id, slab_no))
            coil_material = self._by_coil.get(hr_coil_id)
            if pair_material is None or coil_material is None:
                raise ValueError(f"{label} row identity is missing from material catalog")
            if pair_material is not coil_material:
                raise ValueError(f"{label} row pair/coil catalog disagreement")
            material_key = pair_material.material_key
            if material_key in seen:
                raise ValueError(f"{label} rows resolve to a duplicate material identity")
            seen.add(material_key)
            resolved.append(material_key)
        return tuple(resolved)


@dataclass(frozen=True)
class OperatingRangeSidecar:
    rule_id: str
    contributor_material_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_sha256_uri(self.rule_id, "operating range sidecar rule_id")
        keys = _string_tuple(
            self.contributor_material_keys,
            "operating range contributor material keys",
            require_utf8_sorted=True,
        )
        for key in keys:
            _validate_sha256_uri(key, "operating range contributor material key")
        object.__setattr__(self, "contributor_material_keys", keys)


@dataclass(frozen=True)
class OperatingRangesResult:
    records: tuple[Mapping[str, object], ...]
    sidecars: tuple[OperatingRangeSidecar, ...]

    def __post_init__(self) -> None:
        records = tuple(
            _freeze_json(record, "operating range record") for record in self.records
        )
        sidecars = tuple(self.sidecars)
        if any(type(sidecar) is not OperatingRangeSidecar for sidecar in sidecars):
            raise TypeError("operating range sidecars must use OperatingRangeSidecar")
        record_ids: list[str] = []
        for record in records:
            rule_id = _validate_sha256_uri(record.get("ruleId"), "range record ruleId")
            support = record.get("support")
            if type(support) is not int or support < 0:
                raise ValueError("range record support must be a non-negative built-in integer")
            record_ids.append(rule_id)
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("range record rule IDs must be unique")
        sidecar_ids = [sidecar.rule_id for sidecar in sidecars]
        if record_ids != sidecar_ids:
            raise ValueError("range record and sidecar rule IDs must be one-to-one in the same order")
        for record, sidecar in zip(records, sidecars, strict=True):
            if record["support"] != len(sidecar.contributor_material_keys):
                raise ValueError("range wire support must equal sidecar cardinality")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "sidecars", sidecars)

    def to_wire(self) -> list[dict[str, object]]:
        return [_thaw_json(record) for record in self.records]  # type: ignore[list-item]


@dataclass(frozen=True)
class QualitySplitSidecar:
    adjustment_fields: tuple[str, ...]
    discovery_band_boundaries: Mapping[str, tuple[float, float, float] | None]
    informative_stratum_keys: tuple[str, ...]
    discovery_weights: Mapping[str, float]
    candidate_material_keys: tuple[str, ...]
    comparator_counts_by_stratum: Mapping[str, int]

    def __post_init__(self) -> None:
        fields = _string_tuple(self.adjustment_fields, "quality adjustment fields")
        band_items = _mapping_items(
            self.discovery_band_boundaries, "quality discovery band boundaries"
        )
        expected_band_keys = {field for field in fields if field.endswith("_band")}
        if {key for key, _ in band_items} != expected_band_keys:
            raise ValueError("quality band keys must exactly match selected band fields")
        bands: dict[str, tuple[float, float, float] | None] = {}
        for key, raw_boundary in band_items:
            if raw_boundary is None:
                bands[key] = None
                continue
            try:
                boundary = tuple(raw_boundary)
            except TypeError as error:
                raise TypeError("quality band boundary must be a three-value tuple") from error
            if (
                len(boundary) != 3
                or any(type(value) is not float or not math.isfinite(value) for value in boundary)
                or boundary[0] > boundary[1]
                or boundary[1] > boundary[2]
            ):
                raise ValueError("quality band cuts must be finite built-in floats and nondecreasing")
            bands[key] = boundary  # type: ignore[assignment]

        informative = _string_tuple(
            self.informative_stratum_keys,
            "quality informative stratum keys",
            allow_empty_value=True,
            require_utf8_sorted=True,
        )
        weight_items = _mapping_items(self.discovery_weights, "quality discovery weights")
        weights: dict[str, float] = {}
        for key, value in weight_items:
            if type(value) is not float or not math.isfinite(value) or not 0.0 < value <= 1.0:
                raise ValueError("quality discovery weights must be finite built-in floats in (0,1]")
            weights[key] = value
        if weights and not math.isclose(
            math.fsum(weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError("quality discovery weights must sum to one")
        if not weights and informative:
            raise ValueError("empty quality weights require the no-informative case")

        candidate_keys = _string_tuple(
            self.candidate_material_keys,
            "quality candidate material keys",
            require_utf8_sorted=True,
        )
        for key in candidate_keys:
            _validate_sha256_uri(key, "quality candidate material key")

        count_items = _mapping_items(
            self.comparator_counts_by_stratum,
            "quality comparator counts by stratum",
        )
        counts: dict[str, int] = {}
        for key, value in count_items:
            if type(value) is not int or value <= 0:
                raise ValueError("quality comparator counts must be positive built-in integers")
            counts[key] = value
        if set(counts) != set(informative):
            raise ValueError("quality comparator count keys must match informative strata")

        object.__setattr__(self, "adjustment_fields", fields)
        object.__setattr__(self, "discovery_band_boundaries", MappingProxyType(bands))
        object.__setattr__(self, "informative_stratum_keys", informative)
        object.__setattr__(self, "discovery_weights", MappingProxyType(weights))
        object.__setattr__(self, "candidate_material_keys", candidate_keys)
        object.__setattr__(self, "comparator_counts_by_stratum", MappingProxyType(counts))


@dataclass(frozen=True)
class QualityRuleSidecar:
    rule_id: str
    discovery: QualitySplitSidecar
    confirmation: QualitySplitSidecar

    def __post_init__(self) -> None:
        _validate_sha256_uri(self.rule_id, "quality sidecar rule_id")
        if type(self.discovery) is not QualitySplitSidecar or type(self.confirmation) is not QualitySplitSidecar:
            raise TypeError("quality rule sidecars must contain QualitySplitSidecar values")
        discovery_weight_keys = tuple(self.discovery.discovery_weights)
        if self.discovery.informative_stratum_keys != discovery_weight_keys:
            raise ValueError("discovery informative strata must exactly match discovery weight keys")
        if not set(self.confirmation.informative_stratum_keys).issubset(
            self.discovery.informative_stratum_keys
        ):
            raise ValueError("confirmation informative strata must be a discovery subset")
        if self.confirmation.adjustment_fields != self.discovery.adjustment_fields:
            raise ValueError("confirmation adjustment fields must remain discovery-fixed")
        if dict(self.confirmation.discovery_band_boundaries) != dict(
            self.discovery.discovery_band_boundaries
        ):
            raise ValueError("confirmation band boundaries must remain discovery-fixed")
        if dict(self.confirmation.discovery_weights) != dict(self.discovery.discovery_weights):
            raise ValueError("confirmation weights must remain discovery-fixed")


@dataclass(frozen=True)
class QualityRulesResult:
    records: tuple[Mapping[str, object], ...]
    sidecars: tuple[QualityRuleSidecar, ...]

    def __post_init__(self) -> None:
        records = tuple(
            _freeze_json(record, "quality rule record") for record in self.records
        )
        sidecars = tuple(self.sidecars)
        if any(type(sidecar) is not QualityRuleSidecar for sidecar in sidecars):
            raise TypeError("quality rule sidecars must use QualityRuleSidecar")
        record_ids: list[str] = []
        for record in records:
            rule_id = _validate_sha256_uri(record.get("ruleId"), "quality record ruleId")
            for split_name in ("discovery", "confirmation"):
                metric = record.get(split_name)
                if not isinstance(metric, Mapping):
                    raise ValueError(f"quality record {split_name} metric must be a mapping")
                support = metric.get("support")
                if type(support) is not int or support < 0:
                    raise ValueError("quality metric support must be a non-negative built-in integer")
            record_ids.append(rule_id)
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("quality record rule IDs must be unique")
        sidecar_ids = [sidecar.rule_id for sidecar in sidecars]
        if record_ids != sidecar_ids:
            raise ValueError("quality record and sidecar rule IDs must be one-to-one in the same order")
        for record, sidecar in zip(records, sidecars, strict=True):
            if record["discovery"]["support"] != len(
                sidecar.discovery.candidate_material_keys
            ):
                raise ValueError("quality discovery wire support must equal sidecar cardinality")
            if record["confirmation"]["support"] != len(
                sidecar.confirmation.candidate_material_keys
            ):
                raise ValueError("quality confirmation wire support must equal sidecar cardinality")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "sidecars", sidecars)

    def to_wire(self) -> list[dict[str, object]]:
        return [_thaw_json(record) for record in self.records]  # type: ignore[list-item]


@dataclass(frozen=True)
class PopulationLineage:
    population_ref: str
    split: str
    material_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        accepted = {"REFERENCE", "DISCOVERY", "CONFIRMATION", "HOLDOUT"}
        if type(self.population_ref) is not str or self.population_ref not in accepted:
            raise ValueError("population_ref must be a fixed population name")
        if type(self.split) is not str or self.split != self.population_ref:
            raise ValueError("population split must equal population_ref")
        keys = _string_tuple(
            self.material_keys,
            "population material keys",
            require_utf8_sorted=True,
        )
        for key in keys:
            _validate_sha256_uri(key, "population material key")
        object.__setattr__(self, "material_keys", keys)

    def to_wire(self) -> dict[str, object]:
        return {
            "populationRef": self.population_ref,
            "split": self.split,
            "materialKeys": list(self.material_keys),
        }


@dataclass(frozen=True)
class AggregateLineage:
    artifact_role: str
    rule_id: str
    split: str
    population_ref: str
    input_material_keys: tuple[str, ...]
    comparator_definition: str
    filters: tuple[str, ...]
    transformations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.artifact_role not in {
            "equipment_operating_ranges",
            "quality_risk_intervals",
        }:
            raise ValueError("aggregate artifact_role is invalid")
        _validate_sha256_uri(self.rule_id, "aggregate rule_id")
        if self.split not in {"REFERENCE", "DISCOVERY", "CONFIRMATION"}:
            raise ValueError("aggregate split is invalid")
        if self.population_ref != self.split:
            raise ValueError("aggregate population_ref must match split")
        if self.artifact_role == "equipment_operating_ranges":
            if self.split != "REFERENCE" or self.comparator_definition != "NOT_APPLICABLE":
                raise ValueError("range aggregate must use REFERENCE/NOT_APPLICABLE")
        elif self.comparator_definition != "FIXED_POPULATION_STRATA_MINUS_CANDIDATE":
            raise ValueError("quality aggregate comparator definition is invalid")
        keys = _string_tuple(
            self.input_material_keys,
            "aggregate input material keys",
            require_utf8_sorted=True,
        )
        for key in keys:
            _validate_sha256_uri(key, "aggregate input material key")
        filters = _string_tuple(self.filters, "aggregate filters")
        transformations = _string_tuple(
            self.transformations, "aggregate transformations"
        )
        accepted_filters = {
            "STAGE_AVAILABLE_AT_AS_OF",
            "LABEL_AVAILABLE_AND_MATURE",
            "FINITE_VALUE",
            "PREDICATE_MATCH",
            "FIXED_DISCOVERY_STRATA",
            "INFORMATIVE_STRATA_ONLY",
        }
        accepted_transformations = {
            "TYPE1_QUANTILE",
            "WILSON_SCORE_INTERVAL",
            "BENJAMINI_HOCHBERG_FDR",
        }
        if not set(filters).issubset(accepted_filters):
            raise ValueError("aggregate filters contain an unknown value")
        if not set(transformations).issubset(accepted_transformations):
            raise ValueError("aggregate transformations contain an unknown value")
        object.__setattr__(self, "input_material_keys", keys)
        object.__setattr__(self, "filters", filters)
        object.__setattr__(self, "transformations", transformations)

    def to_wire(self) -> dict[str, object]:
        return {
            "artifactRole": self.artifact_role,
            "ruleId": self.rule_id,
            "split": self.split,
            "populationRef": self.population_ref,
            "inputMaterialKeys": list(self.input_material_keys),
            "comparatorDefinition": self.comparator_definition,
            "filters": list(self.filters),
            "transformations": list(self.transformations),
        }


@dataclass(frozen=True)
class AnalysisConfig:
    schema_version: str
    analysis_config_version: str
    timezone: str
    label_maturity_days: int
    reference_fraction: float
    discovery_fraction: float
    operating_ranges: Mapping[str, object]
    quality_risk: Mapping[str, object]
    bootstrap: Mapping[str, object]
    wilson_z: float
    fields: tuple[Mapping[str, object], ...]
    range_context_hierarchies: Mapping[str, tuple[tuple[str, ...], ...]]
    risk_adjustment_hierarchies: Mapping[str, tuple[tuple[str, ...], ...]]
    fixed_interactions: tuple[tuple[str, str], ...]
    fdr_families: tuple[str, ...]
    evidence_families: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "operating_ranges", _freeze(self.operating_ranges))
        object.__setattr__(self, "quality_risk", _freeze(self.quality_risk))
        object.__setattr__(self, "bootstrap", _freeze(self.bootstrap))
        object.__setattr__(self, "fields", _freeze(self.fields))
        object.__setattr__(
            self, "range_context_hierarchies", _freeze(self.range_context_hierarchies)
        )
        object.__setattr__(
            self, "risk_adjustment_hierarchies", _freeze(self.risk_adjustment_hierarchies)
        )
        object.__setattr__(self, "fixed_interactions", _freeze(self.fixed_interactions))
        object.__setattr__(self, "fdr_families", tuple(self.fdr_families))
        object.__setattr__(self, "evidence_families", tuple(self.evidence_families))


@dataclass(frozen=True)
class InputTables:
    sm_cc: pd.DataFrame
    fur_hr: pd.DataFrame
    ap: pd.DataFrame
    sources: tuple[SourceFile, ...]


@dataclass(frozen=True)
class GenealogyResult:
    boundary_rows: pd.DataFrame
    replay_rows: pd.DataFrame
    quality_rows: pd.DataFrame
    quarantine_rows: pd.DataFrame
    lineage_rows: pd.DataFrame
    audit: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "audit", _freeze(self.audit))


@dataclass(frozen=True)
class TimeSplitResult:
    as_of: date
    discovery_cutoff: date
    reference_rows: pd.DataFrame
    discovery_rows: pd.DataFrame
    confirmation_rows: pd.DataFrame
    holdout_rows: pd.DataFrame
    counts: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", _freeze(self.counts))


def _analysis_config_payload(config: AnalysisConfig) -> dict[str, object]:
    return {
        "analysisConfigVersion": config.analysis_config_version,
        "bootstrap": _thaw_json(config.bootstrap),
        "evidenceFamilies": list(config.evidence_families),
        "fdrFamilies": list(config.fdr_families),
        "fields": _thaw_json(config.fields),
        "fixedInteractions": _thaw_json(config.fixed_interactions),
        "labelMaturityDays": config.label_maturity_days,
        "operatingRanges": _thaw_json(config.operating_ranges),
        "qualityRisk": _thaw_json(config.quality_risk),
        "rangeContextHierarchies": [
            {
                "equipmentType": equipment_type,
                "levels": _thaw_json(levels),
            }
            for equipment_type, levels in config.range_context_hierarchies.items()
        ],
        "riskAdjustmentHierarchies": [
            {
                "equipmentType": equipment_type,
                "levels": _thaw_json(levels),
            }
            for equipment_type, levels in config.risk_adjustment_hierarchies.items()
        ],
        "schemaVersion": config.schema_version,
        "splits": {
            "discoveryFraction": config.discovery_fraction,
            "referenceFraction": config.reference_fraction,
        },
        "timezone": config.timezone,
        "wilsonZ": config.wilson_z,
    }


@dataclass(frozen=True)
class SummaryBuildRequest:
    analysis_config: AnalysisConfig
    analysis_config_bytes: bytes
    producer_runtime_bytes: bytes
    schema_digests: Mapping[str, str]
    inputs: InputTables
    genealogy: GenealogyResult
    split: TimeSplitResult
    material_catalog: tuple[MaterialLineage, ...]
    definitions: tuple[object, ...]
    criteria_identity: Identity
    bundle_identity: Identity
    operating_ranges: OperatingRangesResult
    quality_rules: QualityRulesResult
    events: tuple[object, ...]

    def __post_init__(self) -> None:
        from equipment_quality.deterministic import canonical_json_bytes, sha256_uri
        from equipment_quality.event_builder import ReplayEvent
        from equipment_quality.feature_roles import FeatureDefinition, definitions

        if type(self.analysis_config) is not AnalysisConfig:
            raise TypeError("analysis_config must be an exact AnalysisConfig")
        for label, value in (
            ("analysis_config_bytes", self.analysis_config_bytes),
            ("producer_runtime_bytes", self.producer_runtime_bytes),
        ):
            if type(value) is not bytes:
                raise TypeError(f"{label} must be built-in bytes")
        if canonical_json_bytes(_analysis_config_payload(self.analysis_config)) != (
            self.analysis_config_bytes
        ):
            raise ValueError(
                "analysis_config bytes do not match the frozen AnalysisConfig"
            )
        schema_items = _mapping_items(self.schema_digests, "summary schema digests")
        expected_schema_roles = {
            "bundle_manifest",
            "analysis_config",
            "producer_runtime",
            "equipment_operating_ranges",
            "quality_risk_intervals",
            "analysis_summary",
            "replay_events",
        }
        if {key for key, _ in schema_items} != expected_schema_roles:
            raise ValueError("summary schema roles must be the exact seven roles")
        schemas: dict[str, str] = {}
        for key, value in schema_items:
            schemas[key] = _validate_sha256_uri(value, f"summary schema digest {key}")

        if type(self.inputs) is not InputTables:
            raise TypeError("inputs must be an exact InputTables")
        sources = tuple(self.inputs.sources)
        if len(sources) != 3 or any(
            type(source) is not SourceFile for source in sources
        ):
            raise TypeError("input sources must contain exactly three SourceFile values")
        if tuple(source.role for source in sources) != ("sm_cc", "fur_hr", "ap"):
            raise ValueError("input source roles must use the fixed order")
        if tuple(source.name for source in sources) != (
            "sts_1sm_cc_1.csv",
            "sts_2fur_hr_2.csv",
            "sts_3ap_3.csv",
        ):
            raise ValueError("input source names must use fixed role/name triples")
        input_snapshot = InputTables(
            sm_cc=self.inputs.sm_cc.copy(deep=True),
            fur_hr=self.inputs.fur_hr.copy(deep=True),
            ap=self.inputs.ap.copy(deep=True),
            sources=sources,
        )
        if type(self.genealogy) is not GenealogyResult:
            raise TypeError("genealogy must be an exact GenealogyResult")
        genealogy_snapshot = GenealogyResult(
            boundary_rows=self.genealogy.boundary_rows.copy(deep=True),
            replay_rows=self.genealogy.replay_rows.copy(deep=True),
            quality_rows=self.genealogy.quality_rows.copy(deep=True),
            quarantine_rows=self.genealogy.quarantine_rows.copy(deep=True),
            lineage_rows=self.genealogy.lineage_rows.copy(deep=True),
            audit=self.genealogy.audit,
        )
        if type(self.split) is not TimeSplitResult:
            raise TypeError("split must be an exact TimeSplitResult")
        if type(self.split.as_of) is not date or type(self.split.discovery_cutoff) is not date:
            raise TypeError("split boundaries must be exact dates")
        split_snapshot = TimeSplitResult(
            as_of=self.split.as_of,
            discovery_cutoff=self.split.discovery_cutoff,
            reference_rows=self.split.reference_rows.copy(deep=True),
            discovery_rows=self.split.discovery_rows.copy(deep=True),
            confirmation_rows=self.split.confirmation_rows.copy(deep=True),
            holdout_rows=self.split.holdout_rows.copy(deep=True),
            counts=self.split.counts,
        )
        catalog = tuple(self.material_catalog)
        if any(type(material) is not MaterialLineage for material in catalog):
            raise TypeError("material_catalog must contain exact MaterialLineage values")
        if len({material.material_key for material in catalog}) != len(catalog):
            raise ValueError("material_catalog material keys must be unique")
        _MaterialCatalogIndex(catalog)
        definition_snapshot = tuple(self.definitions)
        if any(type(item) is not FeatureDefinition for item in definition_snapshot):
            raise TypeError("definitions must contain exact FeatureDefinition values")
        if definition_snapshot != definitions(self.analysis_config):
            raise ValueError("definitions must be derived from the frozen analysis config")
        if type(self.criteria_identity) is not Identity or self.criteria_identity.version != "sfep-criteria-id/v1":
            raise TypeError("criteria_identity must be a criteria Identity")
        if type(self.bundle_identity) is not Identity or self.bundle_identity.version != "sfep-bundle-id/v1":
            raise TypeError("bundle_identity must be a bundle Identity")
        if self.bundle_identity.fields.get("criteria_id") != self.criteria_identity.value:
            raise ValueError("bundle identity must bind the criteria identity")
        criteria_fields = self.criteria_identity.fields
        bundle_fields = self.bundle_identity.fields
        if criteria_fields.get("as_of") != self.split.as_of.isoformat():
            raise ValueError("criteria identity as_of must match the split")
        for label, payload in (
            ("analysis_config", self.analysis_config_bytes),
            ("producer_runtime", self.producer_runtime_bytes),
        ):
            digest = sha256_uri(payload)
            if criteria_fields.get(label + "_sha256") != digest or bundle_fields.get(
                label + "_sha256"
            ) != digest:
                raise ValueError(f"{label} bytes do not match both identities")
        for role, digest in schemas.items():
            bundle_key = f"schema.{role}.sha256"
            criteria_key = bundle_key
            if bundle_fields.get(bundle_key) != digest or (
                criteria_key in criteria_fields
                and criteria_fields.get(criteria_key) != digest
            ):
                raise ValueError("schema descriptors do not match both identities")
        for source in sources:
            prefix = f"source.{source.role}"
            if (
                bundle_fields.get(prefix + ".name") != source.name
                or bundle_fields.get(prefix + ".size_bytes") != str(source.size_bytes)
                or bundle_fields.get(prefix + ".sha256") != source.sha256
            ):
                raise ValueError("source descriptors do not match the bundle identity")
        if type(self.operating_ranges) is not OperatingRangesResult:
            raise TypeError("operating_ranges must be an OperatingRangesResult")
        if type(self.quality_rules) is not QualityRulesResult:
            raise TypeError("quality_rules must be a QualityRulesResult")
        event_snapshot = tuple(self.events)
        if any(type(event) is not ReplayEvent for event in event_snapshot):
            raise TypeError("events must contain exact ReplayEvent values")
        if any(
            event.bundle_id != self.bundle_identity.value
            or event.criteria_id != self.criteria_identity.value
            for event in event_snapshot
        ):
            raise ValueError("events must bind both request identities")

        object.__setattr__(self, "schema_digests", MappingProxyType(schemas))
        object.__setattr__(self, "inputs", input_snapshot)
        object.__setattr__(self, "genealogy", genealogy_snapshot)
        object.__setattr__(self, "split", split_snapshot)
        object.__setattr__(self, "material_catalog", catalog)
        object.__setattr__(self, "definitions", definition_snapshot)
        object.__setattr__(self, "events", event_snapshot)


@dataclass(frozen=True)
class BundleWriteRequest:
    output_root: Path
    analysis_config: bytes
    producer_runtime: bytes
    equipment_operating_ranges: Mapping[str, object]
    quality_risk_intervals: Mapping[str, object]
    replay_events: tuple[object, ...]
    analysis_summary: Mapping[str, object]
    criteria_identity: Identity
    bundle_identity: Identity
    sources: tuple[SourceFile, ...]
    schema_digests: Mapping[str, str]
    as_of: date
    timezone: str
    label_maturity_days: int

    def __post_init__(self) -> None:
        from equipment_quality.deterministic import id_lines, sha256_uri
        from equipment_quality.event_builder import ReplayEvent

        if not isinstance(self.output_root, Path):
            raise TypeError("output_root must be a pathlib Path")
        output_root = Path(str(self.output_root))
        if not output_root.is_absolute():
            raise ValueError("output_root must be absolute")
        for label, payload in (
            ("analysis_config", self.analysis_config),
            ("producer_runtime", self.producer_runtime),
        ):
            if type(payload) is not bytes:
                raise TypeError(f"{label} must be built-in bytes")

        if type(self.criteria_identity) is not Identity or (
            self.criteria_identity.version != "sfep-criteria-id/v1"
        ):
            raise TypeError("criteria_identity must be an exact criteria Identity")
        if type(self.bundle_identity) is not Identity or (
            self.bundle_identity.version != "sfep-bundle-id/v1"
        ):
            raise TypeError("bundle_identity must be an exact bundle Identity")
        if type(self.as_of) is not date:
            raise TypeError("as_of must be an exact date")
        for identity in (self.criteria_identity, self.bundle_identity):
            expected = sha256_uri(id_lines(identity.version, identity.fields))
            if identity.value != expected:
                raise ValueError("identity value does not match its frozen fields")

        sources = tuple(self.sources)
        expected_source_roles = ("sm_cc", "fur_hr", "ap")
        expected_source_names = (
            "sts_1sm_cc_1.csv",
            "sts_2fur_hr_2.csv",
            "sts_3ap_3.csv",
        )
        if len(sources) != 3 or any(
            type(source) is not SourceFile for source in sources
        ):
            raise TypeError("sources must contain exactly three SourceFile values")
        if tuple(source.role for source in sources) != expected_source_roles:
            raise ValueError("source roles must use the fixed order")
        if tuple(source.name for source in sources) != expected_source_names:
            raise ValueError("source names must use the fixed role/name triples")
        if any(
            type(source.role) is not str
            or type(source.name) is not str
            or type(source.size_bytes) is not int
            or source.size_bytes < 0
            or type(source.sha256) is not str
            for source in sources
        ):
            raise TypeError("source descriptors must use exact built-in field types")

        schema_items = _mapping_items(self.schema_digests, "bundle schema digests")
        expected_schema_roles = {
            "bundle_manifest",
            "analysis_config",
            "producer_runtime",
            "equipment_operating_ranges",
            "quality_risk_intervals",
            "analysis_summary",
            "replay_events",
        }
        if {key for key, _ in schema_items} != expected_schema_roles:
            raise ValueError("bundle schema digests must use the exact seven roles")
        schemas = {
            key: _validate_sha256_uri(value, f"bundle schema digest {key}")
            for key, value in schema_items
        }

        criteria_fields = dict(self.criteria_identity.fields)
        bundle_fields = dict(self.bundle_identity.fields)
        if len(criteria_fields) != 8 or len(bundle_fields) != 19:
            raise ValueError("bundle request identities have invalid field cardinality")
        if criteria_fields.get("as_of") != self.as_of.isoformat():
            raise ValueError("criteria identity as_of does not match request")
        if bundle_fields.get("criteria_id") != self.criteria_identity.value:
            raise ValueError("bundle identity does not bind criteria identity")
        for role, digest in schemas.items():
            bundle_key = f"schema.{role}.sha256"
            if bundle_fields.get(bundle_key) != digest:
                raise ValueError("bundle identity schema digests do not match request")
            criteria_key = f"schema.{role}.sha256"
            if criteria_key in criteria_fields and criteria_fields[criteria_key] != digest:
                raise ValueError("criteria identity schema digests do not match request")
        for source in sources:
            prefix = f"source.{source.role}"
            if (
                bundle_fields.get(prefix + ".name") != source.name
                or bundle_fields.get(prefix + ".size_bytes") != str(source.size_bytes)
                or bundle_fields.get(prefix + ".sha256") != source.sha256
            ):
                raise ValueError("bundle identity source descriptors do not match request")
        for label, payload in (
            ("analysis_config", self.analysis_config),
            ("producer_runtime", self.producer_runtime),
        ):
            digest = sha256_uri(payload)
            if criteria_fields.get(label + "_sha256") != digest or bundle_fields.get(
                label + "_sha256"
            ) != digest:
                raise ValueError(f"{label} bytes do not match request identities")

        if type(self.timezone) is not str or self.timezone != "Asia/Seoul":
            raise ValueError("timezone must be Asia/Seoul")
        if type(self.label_maturity_days) is not int or self.label_maturity_days != 38:
            raise ValueError("label_maturity_days must be the built-in integer 38")

        ranges = _freeze_json(
            self.equipment_operating_ranges, "equipment_operating_ranges"
        )
        rules = _freeze_json(self.quality_risk_intervals, "quality_risk_intervals")
        summary = _freeze_json(self.analysis_summary, "analysis_summary")
        for label, payload in (
            ("equipment_operating_ranges", ranges),
            ("quality_risk_intervals", rules),
            ("analysis_summary", summary),
        ):
            if not isinstance(payload, Mapping):
                raise TypeError(f"{label} must be a mapping")
            if payload.get("criteriaId") != self.criteria_identity.value:
                raise ValueError(f"{label} does not bind criteria identity")
            if payload.get("asOf") != self.as_of.isoformat():
                raise ValueError(f"{label} does not bind request as_of")
        if summary.get("bundleId") != self.bundle_identity.value:
            raise ValueError("analysis_summary does not bind bundle identity")

        events = tuple(self.replay_events)
        if any(type(event) is not ReplayEvent for event in events):
            raise TypeError("replay_events must contain exact ReplayEvent values")
        if any(
            event.bundle_id != self.bundle_identity.value
            or event.criteria_id != self.criteria_identity.value
            for event in events
        ):
            raise ValueError("replay events do not bind request identities")

        object.__setattr__(self, "output_root", output_root)
        object.__setattr__(self, "equipment_operating_ranges", ranges)
        object.__setattr__(self, "quality_risk_intervals", rules)
        object.__setattr__(self, "analysis_summary", summary)
        object.__setattr__(self, "replay_events", events)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "schema_digests", MappingProxyType(schemas))
