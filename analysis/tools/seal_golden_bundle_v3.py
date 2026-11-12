"""Regenerate the synthetic v3 bundle and Python-to-Java inference vectors."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from equipment_quality.artifacts import atomic_rename
from equipment_quality.deterministic import canonical_json_bytes, sha256_uri


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE = REPOSITORY / "contracts/equipment-monitor/v3/golden-source"
CONFIG = REPOSITORY / "analysis/analysis_config_v3.json"
SOURCE_DIGESTS = {
    "sts_1sm_cc_1.csv": "sha256:3e98bd3385bae05a6e1f66f38be36ab3364ee7fd55080a6dcaa5bb1ac0d2a456",
    "sts_2fur_hr_2.csv": "sha256:f11eb88e62ae998472dbcd861fdde5921521b211205325c271744b97b56aae08",
    "sts_3ap_3.csv": "sha256:a266f54b3375fc0c539c21bc71eba046e71962bbbf022d87494161f4c8c7234c",
}


def _produce(runtime: Path, sources: Path, output: Path) -> Path:
    result = subprocess.run(
        [sys.executable, "-B", "-P", "-m", "equipment_quality.cli", "--config", str(CONFIG),
         "--runtime-manifest", str(runtime), "--data-dir", str(sources), "--output-dir", str(output)],
        env={"PYTHONHASHSEED": "0", "TZ": "Asia/Seoul", "PATH": "/usr/bin:/bin"},
        check=True, stdout=subprocess.PIPE, text=True,
    )
    bundle = Path(result.stdout.strip())
    if bundle.parent != output or not bundle.is_dir():
        raise ValueError("producer did not report a bundle in the requested output directory")
    return bundle


def _bundle_files(bundle: Path) -> dict[str, bytes]:
    return {file.name: file.read_bytes() for file in bundle.iterdir()}


def _golden_vectors(bundle: Path, sources: Path) -> bytes:
    from equipment_quality.genealogy import build_genealogy
    from equipment_quality.model_cli import _test_vectors
    from equipment_quality.model_export import onnx_probabilities
    from equipment_quality.model_features import FeatureContract, encode_model_rows
    from equipment_quality.schema import load_analysis_config, read_inputs
    from equipment_quality.time_split import build_time_split

    wire = json.loads((bundle / "quality_model.json").read_bytes())["features"]
    features = wire["features"]
    contract = FeatureContract(
        tuple((item["name"], item["median"]) for item in features if item["type"] == "numeric"),
        tuple((item["name"], tuple(item["vocabulary"])) for item in features if item["type"] == "categorical"),
        wire["maxMissingFeatureCount"],
    )
    split = build_time_split(build_genealogy(read_inputs(sources)), load_analysis_config(CONFIG))
    holdout = encode_model_rows(split.holdout_rows, contract, require_labels=True)
    matrix = holdout.matrix[holdout.evaluable][:10]
    if not len(matrix):
        raise ValueError("synthetic golden source has no evaluable rows")
    probability = onnx_probabilities((bundle / "quality_model.onnx").read_bytes(), matrix)[:, 1]
    return canonical_json_bytes(_test_vectors(
        split.holdout_rows.loc[holdout.evaluable], matrix.tolist(), probability.tolist(),
        [item["name"] for item in features],
    ))


def seal_golden_bundle(runtime: Path, output: Path) -> None:
    output = output.parent.resolve(strict=True) / output.name
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(dir=output.parent) as work, tempfile.TemporaryDirectory(dir=output.parent) as staged:
        sources = Path(work) / "sources"
        sources.mkdir()
        for name, digest in SOURCE_DIGESTS.items():
            payload = (SOURCE / name).read_bytes()
            if sha256_uri(payload) != digest:
                raise ValueError(f"unexpected synthetic source digest for {name}")
            (sources / name).write_bytes(payload)
        first = _produce(runtime, sources, Path(work) / "first")
        second = _produce(runtime, sources, Path(work) / "second")
        if first.name != second.name or _bundle_files(first) != _bundle_files(second):
            raise ValueError("independent v3 bundle generation did not match")
        stage = Path(staged)
        shutil.copytree(first, stage / "golden-bundle")
        expectation = stage / "golden-expectation"
        expectation.mkdir()
        (expectation / "quality_model_test_vectors.json").write_bytes(_golden_vectors(first, sources))
        atomic_rename(stage, output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    seal_golden_bundle(args.runtime_manifest.resolve(strict=True), args.output)


if __name__ == "__main__":
    main()
