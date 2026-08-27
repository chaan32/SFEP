"""Frozen data contracts shared by the offline producer stages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import math
from pathlib import PurePath
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
