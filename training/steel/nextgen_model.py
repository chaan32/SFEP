from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd


def frozen_percentile(scores: np.ndarray, reference: np.ndarray) -> np.ndarray:
    values = np.asarray(scores, dtype=float)
    sorted_reference = np.sort(np.asarray(reference, dtype=float))
    if len(sorted_reference) == 0 or not np.isfinite(sorted_reference).all():
        raise ValueError("frozen percentile reference must be finite and non-empty")
    left = np.searchsorted(sorted_reference, values, side="left")
    right = np.searchsorted(sorted_reference, values, side="right")
    result = right.astype(float) / len(sorted_reference)
    tied = right > left
    result[tied] = (left[tied] + right[tied] + 1.0) / (2.0 * len(sorted_reference))
    return result


class FrozenRankAverageEnsemble:
    def __init__(
        self,
        first_predictor,
        second_predictor,
        first_reference_scores: np.ndarray,
        second_reference_scores: np.ndarray,
        feature_names: list[str],
        integrity_probe: pd.DataFrame | None = None,
    ) -> None:
        self.first_predictor = first_predictor
        self.second_predictor = second_predictor
        self.first_reference_scores = np.sort(
            np.asarray(first_reference_scores, dtype=float)
        )
        self.second_reference_scores = np.sort(
            np.asarray(second_reference_scores, dtype=float)
        )
        if (
            len(self.first_reference_scores) == 0
            or len(self.second_reference_scores) == 0
            or not np.isfinite(self.first_reference_scores).all()
            or not np.isfinite(self.second_reference_scores).all()
        ):
            raise ValueError("rank ensemble reference scores must be finite and non-empty")
        self.feature_names_in_ = np.asarray(feature_names, dtype=object)
        self.classes_ = np.asarray([0, 1])
        self.integrity_probe = (
            None
            if integrity_probe is None
            else integrity_probe[list(self.feature_names_in_)].reset_index(drop=True).copy()
        )

    @staticmethod
    def _raw_score(predictor, features: pd.DataFrame) -> np.ndarray:
        expected = list(np.asarray(predictor.feature_names_in_, dtype=object))
        return np.asarray(predictor.predict_proba(features[expected]), dtype=float)[:, 1]

    @staticmethod
    def _percentile(scores: np.ndarray, reference: np.ndarray) -> np.ndarray:
        return frozen_percentile(scores, reference)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        missing = sorted(set(self.feature_names_in_) - set(features.columns))
        if missing:
            raise ValueError(f"rank ensemble is missing features: {', '.join(missing)}")
        first = self._raw_score(self.first_predictor, features)
        second = self._raw_score(self.second_predictor, features)
        positive = 0.5 * (
            self._percentile(first, self.first_reference_scores)
            + self._percentile(second, self.second_reference_scores)
        )
        return np.column_stack([1.0 - positive, positive])


def numeric_array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(str(array.shape).encode("ascii"))
    digest.update(array.tobytes())
    return digest.hexdigest()


def _strings_sha256(values: np.ndarray | list[object]) -> str:
    encoded = json.dumps(
        [str(value) for value in np.asarray(values, dtype=object).tolist()],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ensemble_integrity_metadata(
    predictor: FrozenRankAverageEnsemble,
) -> dict[str, object]:
    if predictor.integrity_probe is None or predictor.integrity_probe.empty:
        raise ValueError("ensemble integrity requires a non-empty frozen probe")
    first_probe_scores = predictor._raw_score(
        predictor.first_predictor, predictor.integrity_probe
    )
    second_probe_scores = predictor._raw_score(
        predictor.second_predictor, predictor.integrity_probe
    )
    if not np.isfinite(first_probe_scores).all() or not np.isfinite(
        second_probe_scores
    ).all():
        raise ValueError("ensemble integrity probe produced non-finite scores")
    return {
        "child_probe_prediction_sha256": [
            numeric_array_sha256(first_probe_scores),
            numeric_array_sha256(second_probe_scores),
        ],
        "child_feature_names_sha256": [
            _strings_sha256(predictor.first_predictor.feature_names_in_),
            _strings_sha256(predictor.second_predictor.feature_names_in_),
        ],
        "reference_score_sha256": [
            numeric_array_sha256(predictor.first_reference_scores),
            numeric_array_sha256(predictor.second_reference_scores),
        ],
        "feature_names_sha256": _strings_sha256(predictor.feature_names_in_),
    }


def validate_ensemble_integrity(artifact: dict[str, object]) -> None:
    children = artifact.get("ensemble_children")
    integrity = artifact.get("ensemble_integrity")
    if children is None:
        if integrity is not None:
            raise ValueError("ensemble integrity metadata exists without ensemble children")
        return
    predictor = artifact.get("predictor")
    if not isinstance(predictor, FrozenRankAverageEnsemble):
        raise ValueError("ensemble integrity requires a FrozenRankAverageEnsemble")
    if not isinstance(children, list) or len(children) != 2:
        raise ValueError("ensemble integrity requires exactly two child names")
    if integrity != ensemble_integrity_metadata(predictor):
        raise ValueError("ensemble integrity metadata mismatch")
