"""Frozen data contracts shared by the offline producer stages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath
from types import MappingProxyType
import pandas as pd


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


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
    discovery_cutoff: date | None
    reference_rows: pd.DataFrame
    discovery_rows: pd.DataFrame
    confirmation_rows: pd.DataFrame
    holdout_rows: pd.DataFrame
    counts: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", _freeze(self.counts))
