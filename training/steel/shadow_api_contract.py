from __future__ import annotations

import math
from datetime import datetime

import pandas as pd

from training.steel.shadow_contract import ENGINEERED_FEATURES, validate_label_batch


FEATURE_SCHEMA_VERSION = "steel-shadow-feature-batch-v1"
LABEL_SCHEMA_VERSION = "steel-shadow-label-batch-v1"

_FEATURE_TOP_FIELDS = {"schemaVersion", "requestId", "coils"}
_FEATURE_COIL_FIELDS = {
    "chargeId",
    "slabNo",
    "hrCoilId",
    "hrDate",
    "featureAvailableAt",
    "features",
}
_LABEL_TOP_FIELDS = {
    "schemaVersion",
    "requestId",
    "supersedesLabelBatchId",
    "labels",
}
_LABEL_REQUIRED_TOP_FIELDS = {"schemaVersion", "requestId", "labels"}
_LABEL_FIELDS = {"hrCoilId", "judge", "labelFinalizedAt"}
_FORBIDDEN_FEATURES = {"judge", "ap_date", "label_finalized_at", "dataset_split"}


class ShadowApiContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require_object(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ShadowApiContractError(
            "REQUEST_SCHEMA_INVALID", f"{name} must be a JSON object"
        )
    return value


def _validate_request_id(payload: dict[str, object]) -> str:
    request_id = payload.get("requestId")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        raise ShadowApiContractError(
            "REQUEST_SCHEMA_INVALID",
            "requestId must contain 1 to 128 printable ASCII characters",
        )
    if any(ord(character) < 32 or ord(character) > 126 for character in request_id):
        raise ShadowApiContractError(
            "REQUEST_SCHEMA_INVALID",
            "requestId must contain 1 to 128 printable ASCII characters",
        )
    return request_id


def _validate_top_fields(
    payload: dict[str, object],
    *,
    allowed: set[str],
    required: set[str],
) -> None:
    unknown = sorted(set(payload) - allowed)
    missing = sorted(required - set(payload))
    if unknown:
        raise ShadowApiContractError(
            "REQUEST_SCHEMA_INVALID", "request contains unknown fields: " + ", ".join(unknown)
        )
    if missing:
        raise ShadowApiContractError(
            "REQUEST_SCHEMA_INVALID", "request is missing fields: " + ", ".join(missing)
        )


def _validate_count(value: object, *, field: str, max_coils: int) -> list[object]:
    if not isinstance(value, list):
        raise ShadowApiContractError(
            "REQUEST_SCHEMA_INVALID", f"{field} must be a JSON array"
        )
    if not 1 <= len(value) <= max_coils:
        raise ShadowApiContractError(
            "REQUEST_SCHEMA_INVALID",
            f"{field} must contain 1 to {max_coils} items",
        )
    return value


def _base_feature_order(registry: dict[str, object]) -> list[str]:
    models = registry.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("registry must contain at least one model")
    result: list[str] = []
    for model in models:
        if not isinstance(model, dict):
            raise ValueError("registry model entry must be an object")
        schema = model.get("feature_schema")
        if not isinstance(schema, dict):
            raise ValueError("registry model feature schema is missing")
        included = schema.get("included")
        if not isinstance(included, list) or not all(
            isinstance(feature, str) for feature in included
        ):
            raise ValueError("registry included feature schema is invalid")
        for feature in included:
            if feature not in ENGINEERED_FEATURES and feature not in result:
                result.append(feature)
    if not result:
        raise ValueError("registry contains no caller-supplied model features")
    return result


def _identifier(value: object, *, field: str, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowApiContractError(code, f"{field} must be a non-empty string")
    return value.strip()


def _date(value: object, *, field: str, code: str) -> str:
    if not isinstance(value, str):
        raise ShadowApiContractError(code, f"{field} must use YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ShadowApiContractError(code, f"{field} must use YYYY-MM-DD") from exc
    return parsed.strftime("%Y-%m-%d")


def _timestamp(value: object, *, field: str, code: str) -> str:
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ShadowApiContractError(code, f"{field} must be a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise ShadowApiContractError(code, f"{field} must be timezone-aware")
    return parsed.tz_convert("UTC").isoformat()


def _feature_scalar(value: object, *, feature: str) -> object:
    if isinstance(value, (dict, list, tuple, set)):
        raise ShadowApiContractError(
            "FEATURE_CONTRACT_INVALID", f"feature {feature} must be a scalar value"
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise ShadowApiContractError(
            "FEATURE_CONTRACT_INVALID", f"feature {feature} must be finite or null"
        )
    return value


def parse_feature_request(
    payload: object,
    registry: dict[str, object],
    *,
    max_coils: int,
) -> tuple[str, pd.DataFrame]:
    root = _require_object(payload, name="feature request")
    _validate_top_fields(
        root,
        allowed=_FEATURE_TOP_FIELDS,
        required=_FEATURE_TOP_FIELDS,
    )
    if root.get("schemaVersion") != FEATURE_SCHEMA_VERSION:
        raise ShadowApiContractError(
            "REQUEST_SCHEMA_INVALID", "unsupported feature schemaVersion"
        )
    request_id = _validate_request_id(root)
    coils = _validate_count(root.get("coils"), field="coils", max_coils=max_coils)
    feature_order = _base_feature_order(registry)
    expected_features = set(feature_order)
    rows: list[dict[str, object]] = []
    seen_coils: set[str] = set()
    for index, raw_coil in enumerate(coils):
        coil = _require_object(raw_coil, name=f"coils[{index}]")
        unknown_coil_fields = sorted(set(coil) - _FEATURE_COIL_FIELDS)
        if unknown_coil_fields:
            raise ShadowApiContractError(
                "REQUEST_SCHEMA_INVALID",
                "Coil contains unknown fields: " + ", ".join(unknown_coil_fields),
            )
        missing_coil_fields = sorted(_FEATURE_COIL_FIELDS - set(coil))
        if missing_coil_fields:
            raise ShadowApiContractError(
                "FEATURE_CONTRACT_INVALID",
                "Coil is missing fields: " + ", ".join(missing_coil_fields),
            )
        hr_coil_id = _identifier(
            coil.get("hrCoilId"),
            field="hrCoilId",
            code="FEATURE_CONTRACT_INVALID",
        )
        if hr_coil_id in seen_coils:
            raise ShadowApiContractError(
                "FEATURE_CONTRACT_INVALID", "feature request contains duplicate hrCoilId"
            )
        seen_coils.add(hr_coil_id)
        features = coil.get("features")
        if not isinstance(features, dict):
            raise ShadowApiContractError(
                "FEATURE_CONTRACT_INVALID", "features must be a JSON object"
            )
        supplied = set(features)
        forbidden = sorted(
            feature
            for feature in supplied
            if feature in _FORBIDDEN_FEATURES
            or feature.startswith("ap_")
            or feature in ENGINEERED_FEATURES
        )
        if forbidden:
            raise ShadowApiContractError(
                "FEATURE_CONTRACT_INVALID",
                "features contain forbidden fields: " + ", ".join(forbidden),
            )
        missing_features = sorted(expected_features - supplied)
        unknown_features = sorted(supplied - expected_features)
        if missing_features:
            raise ShadowApiContractError(
                "FEATURE_CONTRACT_INVALID",
                "features are missing required fields: " + ", ".join(missing_features),
            )
        if unknown_features:
            raise ShadowApiContractError(
                "FEATURE_CONTRACT_INVALID",
                "features contain unknown fields: " + ", ".join(unknown_features),
            )
        row = {
            "charge_id": _identifier(
                coil.get("chargeId"),
                field="chargeId",
                code="FEATURE_CONTRACT_INVALID",
            ),
            "slab_no": _identifier(
                coil.get("slabNo"),
                field="slabNo",
                code="FEATURE_CONTRACT_INVALID",
            ),
            "hr_coil_id": hr_coil_id,
            "hr_date": _date(
                coil.get("hrDate"),
                field="hrDate",
                code="FEATURE_CONTRACT_INVALID",
            ),
            "feature_available_at": _timestamp(
                coil.get("featureAvailableAt"),
                field="featureAvailableAt",
                code="FEATURE_CONTRACT_INVALID",
            ),
        }
        row.update(
            {
                feature: _feature_scalar(features[feature], feature=feature)
                for feature in feature_order
            }
        )
        rows.append(row)
    frame = pd.DataFrame(rows).sort_values("hr_coil_id", kind="stable").reset_index(
        drop=True
    )
    return request_id, frame


def parse_label_request(
    payload: object,
    *,
    max_coils: int,
) -> tuple[str, str | None, pd.DataFrame]:
    root = _require_object(payload, name="label request")
    _validate_top_fields(
        root,
        allowed=_LABEL_TOP_FIELDS,
        required=_LABEL_REQUIRED_TOP_FIELDS,
    )
    if root.get("schemaVersion") != LABEL_SCHEMA_VERSION:
        raise ShadowApiContractError(
            "REQUEST_SCHEMA_INVALID", "unsupported label schemaVersion"
        )
    request_id = _validate_request_id(root)
    supersedes_value = root.get("supersedesLabelBatchId")
    supersedes: str | None
    if supersedes_value is None:
        supersedes = None
    else:
        supersedes = _identifier(
            supersedes_value,
            field="supersedesLabelBatchId",
            code="LABEL_CONTRACT_INVALID",
        )
    labels = _validate_count(root.get("labels"), field="labels", max_coils=max_coils)
    rows: list[dict[str, object]] = []
    for index, raw_label in enumerate(labels):
        if not isinstance(raw_label, dict):
            raise ShadowApiContractError(
                "LABEL_CONTRACT_INVALID", f"labels[{index}] must be a JSON object"
            )
        unknown = sorted(set(raw_label) - _LABEL_FIELDS)
        missing = sorted(_LABEL_FIELDS - set(raw_label))
        if unknown:
            raise ShadowApiContractError(
                "LABEL_CONTRACT_INVALID",
                "label contains unknown fields: " + ", ".join(unknown),
            )
        if missing:
            raise ShadowApiContractError(
                "LABEL_CONTRACT_INVALID",
                "label is missing fields: " + ", ".join(missing),
            )
        rows.append(
            {
                "hr_coil_id": _identifier(
                    raw_label.get("hrCoilId"),
                    field="hrCoilId",
                    code="LABEL_CONTRACT_INVALID",
                ),
                "judge": raw_label.get("judge"),
                "label_finalized_at": _timestamp(
                    raw_label.get("labelFinalizedAt"),
                    field="labelFinalizedAt",
                    code="LABEL_CONTRACT_INVALID",
                ),
            }
        )
    try:
        frame = validate_label_batch(pd.DataFrame(rows))
    except ValueError as exc:
        raise ShadowApiContractError("LABEL_CONTRACT_INVALID", str(exc)) from exc
    frame = frame.sort_values("hr_coil_id", kind="stable").reset_index(drop=True)
    return request_id, supersedes, frame


def canonical_csv_bytes(frame: pd.DataFrame) -> bytes:
    canonical = frame.copy()
    if "hr_coil_id" in canonical.columns:
        canonical = canonical.sort_values("hr_coil_id", kind="stable").reset_index(
            drop=True
        )
    return canonical.to_csv(index=False).encode("utf-8-sig")
