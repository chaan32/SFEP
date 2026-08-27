"""Task 10 real-data sealed-runtime test fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest


_BOOTSTRAP = r"""
import json
import sys

site_packages = sys.argv.pop(1)
if not site_packages or not site_packages.endswith('/lib/python3.12/site-packages'):
    raise SystemExit('invalid sealed site-packages')
sys.path.insert(0, site_packages)
from equipment_quality.cli import main

if sys.argv[1:2] == ['__sfep_probe_inputs__']:
    from pathlib import Path
    from equipment_quality.schema import read_inputs
    tables = read_inputs(Path(sys.argv[2]))
    result = {
        'provenanceColumns': {
            role: [str(column) for column in getattr(tables, role).columns if str(column).startswith('_source_')]
            for role in ('ap', 'fur_hr', 'sm_cc')
        },
        'shapes': {
            role: list(getattr(tables, role).shape)
            for role in ('ap', 'fur_hr', 'sm_cc')
        },
    }
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(',', ':')) + '\n')
else:
    raise SystemExit(main(sys.argv[1:]))
""".strip()


@dataclass(frozen=True)
class _SealedResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class _RunEvidence:
    runtime: Path
    output_root: Path
    bundle_root: Path
    bundle_id: str
    snapshot: dict[str, tuple[int, str]]
    json_payloads: dict[str, dict[str, object]]
    volatile_markers: tuple[bytes, ...]

    def payload(self, filename: str) -> bytes:
        assert filename in self.snapshot
        return (self.bundle_root / filename).read_bytes()


@dataclass(frozen=True)
class _BaselineEvidence:
    runs: tuple[_RunEvidence, _RunEvidence]
    cache_before: tuple[tuple[str, ...], tuple[str, ...]]
    cache_after: tuple[tuple[str, ...], tuple[str, ...]]


@dataclass(frozen=True)
class _MutationEvidence:
    population: str
    material_key: str
    data_dir: Path
    record_numbers: dict[str, int]
    changed_files: dict[str, tuple[str, ...]]
    old_values: dict[str, str]
    new_values: dict[str, str]
    original_hashes_before: dict[str, str]
    original_hashes_after: dict[str, str]
    mutated_hashes: dict[str, str]


@dataclass(frozen=True)
class _LeakageEvidence:
    baseline: _RunEvidence
    mutated: _RunEvidence
    mutation: _MutationEvidence


@dataclass(frozen=True)
class _PersistentEvidence:
    baseline: _RunEvidence
    runs: tuple[_RunEvidence, _RunEvidence]
    summary_checkpoints: tuple[dict[str, object], dict[str, object]]


def _bounded_subprocess(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: float = 900.0,
    output_limit: int = 256 * 1024,
) -> _SealedResult:
    """Run without a shell while independently bounding both captured streams."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    chunks: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    overflow = threading.Event()

    def read_stream(name: str, stream) -> None:
        while True:
            block = stream.read(8192)
            if not block:
                return
            target = chunks[name]
            if len(target) + len(block) > output_limit:
                overflow.set()
                process.kill()
                return
            target.extend(block)

    readers = [
        threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise AssertionError(f"sealed subprocess exceeded {timeout_seconds:g}s") from None
    finally:
        for reader in readers:
            reader.join(timeout=5.0)
        process.stdout.close()
        process.stderr.close()
    if overflow.is_set():
        raise AssertionError(f"sealed subprocess output exceeded {output_limit} bytes")
    return _SealedResult(returncode, bytes(chunks["stdout"]), bytes(chunks["stderr"]))


@dataclass(frozen=True)
class _RealHarness:
    data_dir: Path
    runtimes: tuple[Path, Path]
    controller_dir: Path

    @staticmethod
    def _site_packages(runtime: Path) -> Path:
        venv_root = runtime.parent.parent
        configuration = venv_root / "pyvenv.cfg"
        assert configuration.is_file(), f"missing pyvenv.cfg for {runtime}"
        version_lines = [
            line.partition("=")[2].strip()
            for line in configuration.read_text(encoding="utf-8").splitlines()
            if line.partition("=")[0].strip() == "version"
        ]
        assert version_lines == ["3.12.10"], f"unexpected sealed runtime version: {version_lines}"
        site_packages = venv_root / "lib" / "python3.12" / "site-packages"
        assert site_packages.is_dir(), f"missing sealed site-packages: {site_packages}"
        return site_packages

    def _command(self, runtime: Path, arguments: list[str]) -> list[str]:
        assert runtime.is_absolute() and runtime.is_file()
        site_packages = self._site_packages(runtime)
        return [
            os.fspath(runtime),
            "-P",
            "-s",
            "-S",
            "-B",
            "-c",
            _BOOTSTRAP,
            os.fspath(site_packages),
            *arguments,
        ]

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "Asia/Seoul",
        }

    def controller_contract(
        self, runtime: Path
    ) -> tuple[list[str], dict[str, str]]:
        return self._command(runtime, []), self._environment()

    def _run(self, runtime: Path, arguments: list[str]) -> _SealedResult:
        self._require_cache_free(runtime)
        try:
            return _bounded_subprocess(
                self._command(runtime, arguments),
                cwd=self.controller_dir,
                environment=self._environment(),
            )
        finally:
            self._require_cache_free(runtime)

    @staticmethod
    def _snapshot(bundle_root: Path) -> dict[str, tuple[int, str]]:
        entries = sorted(bundle_root.iterdir(), key=lambda path: path.name.encode("utf-8"))
        assert entries and all(path.is_file() for path in entries)
        return {
            path.name: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in entries
        }

    def cache_inventory(self, runtime: Path) -> tuple[str, ...]:
        site_packages = self._site_packages(runtime)
        forbidden = []
        for path in site_packages.rglob("*"):
            if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}:
                forbidden.append(path.relative_to(site_packages).as_posix())
        return tuple(sorted(forbidden, key=lambda value: value.encode("utf-8")))

    def _require_cache_free(self, runtime: Path) -> None:
        forbidden = self.cache_inventory(runtime)
        assert not forbidden, f"sealed runtime bytecode cache paths: {forbidden!r}"

    def run_producer(
        self,
        runtime: Path,
        output_root: Path,
        *,
        data_dir: Path | None = None,
    ) -> _RunEvidence:
        repository_root = Path(__file__).resolve().parents[2]
        config_path = repository_root / "analysis" / "analysis_config.json"
        runtime_manifest = repository_root / "analysis" / "producer_runtime.json"
        effective_data = self.data_dir if data_dir is None else data_dir
        assert config_path.is_file() and runtime_manifest.is_file()
        assert effective_data.is_absolute() and effective_data.is_dir()
        assert output_root.is_absolute() and not output_root.exists()
        result = self._run(
            runtime,
            [
                "--config", os.fspath(config_path),
                "--runtime-manifest", os.fspath(runtime_manifest),
                "--data-dir", os.fspath(effective_data),
                "--output-dir", os.fspath(output_root),
            ],
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        assert result.stderr == b""
        stdout_lines = result.stdout.decode("utf-8").splitlines()
        assert len(stdout_lines) == 1 and stdout_lines[0]
        bundle_root = Path(stdout_lines[0])
        assert bundle_root.is_absolute()
        assert bundle_root.parent == output_root
        assert bundle_root.is_dir()
        snapshot = self._snapshot(bundle_root)
        json_payloads = {
            "bundle_manifest.json": json.loads(
                (bundle_root / "bundle_manifest.json").read_bytes()
            )
        }
        volatile_paths = {
            effective_data,
            effective_data.resolve(),
            runtime,
            runtime.resolve(),
            runtime.parent.parent,
            runtime.parent.parent.resolve(),
            output_root,
            output_root.resolve(),
            output_root.parent,
            output_root.parent.resolve(),
            self.controller_dir,
            self.controller_dir.resolve(),
            repository_root,
            repository_root.resolve(),
            config_path,
            config_path.resolve(),
            runtime_manifest,
            runtime_manifest.resolve(),
        }
        markers = {
            os.fsencode(path) for path in volatile_paths if len(os.fspath(path)) >= 8
        }
        markers.add(date.today().isoformat().encode("ascii"))
        return _RunEvidence(
            runtime=runtime,
            output_root=output_root,
            bundle_root=bundle_root,
            bundle_id=bundle_root.name,
            snapshot=snapshot,
            json_payloads=json_payloads,
            volatile_markers=tuple(sorted(markers)),
        )

    def probe_input_shapes(self) -> dict[str, list[int]]:
        result = self._run(
            self.runtimes[0],
            ["__sfep_probe_inputs__", os.fspath(self.data_dir)],
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        assert result.stderr == b""
        return json.loads(result.stdout)


@pytest.fixture
def sealed_harness_module():
    """Expose only this test controller for cheap fail-closed unit probes."""
    return sys.modules[__name__]


@pytest.fixture(scope="session")
def real_harness(tmp_path_factory: pytest.TempPathFactory) -> _RealHarness:
    """Return the controller-owned real-data harness when inputs are supplied."""
    required = (
        "SFEP_STEEL_DATA_DIR",
        "SFEP_RUNTIME_PYTHON_A",
        "SFEP_RUNTIME_PYTHON_B",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip("real-data environment is absent: " + ", ".join(missing))
    for name in required:
        assert Path(os.environ[name]).is_absolute(), f"{name} must be absolute"
    data_dir = Path(os.environ["SFEP_STEEL_DATA_DIR"])
    runtimes = (
        Path(os.environ["SFEP_RUNTIME_PYTHON_A"]),
        Path(os.environ["SFEP_RUNTIME_PYTHON_B"]),
    )
    assert data_dir.is_dir()
    assert all(runtime.is_file() for runtime in runtimes)
    venv_roots = tuple(runtime.parent.parent for runtime in runtimes)
    assert venv_roots[0] != venv_roots[1], "sealed runtime venv roots must be distinct"
    sites = tuple(_RealHarness._site_packages(runtime) for runtime in runtimes)
    assert sites[0] != sites[1], "sealed site-packages paths must be distinct"
    assert not sites[0].samefile(sites[1]), "sealed site-packages must be physically distinct"
    return _RealHarness(
        data_dir=data_dir,
        runtimes=runtimes,
        controller_dir=tmp_path_factory.mktemp("sfep-real-controller"),
    )


@pytest.fixture(scope="session")
def baseline_runs(
    real_harness: _RealHarness,
    tmp_path_factory: pytest.TempPathFactory,
) -> _BaselineEvidence:
    cache_before = tuple(
        real_harness.cache_inventory(runtime) for runtime in real_harness.runtimes
    )
    parent = tmp_path_factory.mktemp("sfep-real-baseline")
    runs = (
        real_harness.run_producer(real_harness.runtimes[0], parent / "runtime-a"),
        real_harness.run_producer(real_harness.runtimes[1], parent / "runtime-b"),
    )
    cache_after = tuple(
        real_harness.cache_inventory(runtime) for runtime in real_harness.runtimes
    )
    return _BaselineEvidence(
        runs=runs,
        cache_before=cache_before,
        cache_after=cache_after,
    )


_SOURCE_NAMES = (
    "sts_1sm_cc_1.csv",
    "sts_2fur_hr_2.csv",
    "sts_3ap_3.csv",
)


def _source_hashes(data_dir: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((data_dir / name).read_bytes()).hexdigest()
        for name in _SOURCE_NAMES
    }


def _copy_exact_sources(source: Path, destination: Path) -> Path:
    destination.mkdir()
    assert {path.name for path in source.glob("*.csv")} == set(_SOURCE_NAMES)
    for name in _SOURCE_NAMES:
        shutil.copy2(source / name, destination / name)
    assert {path.name for path in destination.iterdir()} == set(_SOURCE_NAMES)
    return destination


def _cp949_rows(payload: bytes) -> list[list[str]]:
    return list(
        csv.reader(io.StringIO(payload.decode("cp949"), newline=""), strict=True)
    )


def _render_cp949_rows(rows: list[list[str]]) -> bytes:
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerows(rows)
    return output.getvalue().encode("cp949")


def _mutate_one_logical_row(
    path: Path,
    *,
    record_number: int,
    match: dict[str, str],
    replacements: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    original_payload = path.read_bytes()
    original_rows = _cp949_rows(original_payload)
    assert _render_cp949_rows(original_rows) == original_payload
    header = original_rows[0]
    assert len(header) == len(set(header))
    columns = {name: header.index(name) for name in {*match, *replacements}}
    matches = [
        index
        for index, row in enumerate(original_rows[1:], start=1)
        if all(row[columns[name]] == value for name, value in match.items())
    ]
    target_index = record_number - 1
    assert record_number >= 2
    assert matches == [target_index]

    mutated_rows = [row.copy() for row in original_rows]
    old_values = {
        name: mutated_rows[target_index][columns[name]] for name in replacements
    }
    assert all(old_values[name] != value for name, value in replacements.items())
    for name, value in replacements.items():
        mutated_rows[target_index][columns[name]] = value

    mutated_payload = _render_cp949_rows(mutated_rows)
    reparsed = _cp949_rows(mutated_payload)
    assert len(reparsed) == len(original_rows)
    for index, (before, after) in enumerate(zip(original_rows, reparsed, strict=True)):
        if index == target_index:
            assert sum(left != right for left, right in zip(before, after, strict=True)) == len(
                replacements
            )
        else:
            assert after == before
    path.write_bytes(mutated_payload)
    return old_values, dict(replacements)


def _linked_material(run: _RunEvidence, population: str) -> dict[str, object]:
    summary = json.loads((run.bundle_root / "analysis_summary.json").read_bytes())
    populations = {
        item["populationRef"]: set(item["materialKeys"])
        for item in summary["lineage"]["populations"]
    }
    assert population in populations
    required_roles = {"sm_cc", "fur_hr", "ap"}
    candidates = [
        material
        for material in summary["lineage"]["materials"]
        if material["materialKey"] in populations[population]
        and {record["role"] for record in material["sourceRecords"]} == required_roles
    ]
    assert candidates
    candidates.sort(key=lambda material: material["materialKey"].encode("utf-8"))
    return candidates[0]


def _mutated_data(
    harness: _RealHarness,
    baseline: _RunEvidence,
    population: str,
    destination: Path,
    *,
    mutate_furnace: bool,
) -> tuple[Path, _MutationEvidence]:
    original_before = _source_hashes(harness.data_dir)
    copied = _copy_exact_sources(harness.data_dir, destination)
    material = _linked_material(baseline, population)
    source_records = {
        record["role"]: record for record in material["sourceRecords"]
    }
    old_values: dict[str, str] = {}
    new_values: dict[str, str] = {}
    changed_files: dict[str, tuple[str, ...]] = {}

    if mutate_furnace:
        furnace_path = copied / "sts_2fur_hr_2.csv"
        furnace_rows = _cp949_rows(furnace_path.read_bytes())
        furnace_target = furnace_rows[int(source_records["fur_hr"]["recordNumber"]) - 1]
        furnace_header = furnace_rows[0]
        current = furnace_target[furnace_header.index("f_heat_temp")]
        try:
            changed_number = Decimal(current) + Decimal("0.125")
        except InvalidOperation:
            raise AssertionError("selected HOLDOUT f_heat_temp must be numeric") from None
        assert changed_number.is_finite()
        changed = format(changed_number, "f")
        before, after = _mutate_one_logical_row(
            furnace_path,
            record_number=int(source_records["fur_hr"]["recordNumber"]),
            match={
                "charge_id": str(material["chargeId"]),
                "slab_no": str(material["slabNo"]),
                "hr_coil_id": str(material["hrCoilId"]),
            },
            replacements={"f_heat_temp": changed},
        )
        old_values.update({f"fur_hr.{key}": value for key, value in before.items()})
        new_values.update({f"fur_hr.{key}": value for key, value in after.items()})
        changed_files[furnace_path.name] = ("f_heat_temp",)

    ap_path = copied / "sts_3ap_3.csv"
    ap_rows = _cp949_rows(ap_path.read_bytes())
    ap_target = ap_rows[int(source_records["ap"]["recordNumber"]) - 1]
    ap_header = ap_rows[0]
    judge = ap_target[ap_header.index("judge")]
    assert judge in {"양품", "불량"}
    flipped = "불량" if judge == "양품" else "양품"
    before, after = _mutate_one_logical_row(
        ap_path,
        record_number=int(source_records["ap"]["recordNumber"]),
        match={"hr_coil_id": str(material["hrCoilId"])},
        replacements={"judge": flipped},
    )
    old_values.update({f"ap.{key}": value for key, value in before.items()})
    new_values.update({f"ap.{key}": value for key, value in after.items()})
    changed_files[ap_path.name] = ("judge",)

    original_after = _source_hashes(harness.data_dir)
    assert original_after == original_before
    mutation = _MutationEvidence(
        population=population,
        material_key=str(material["materialKey"]),
        data_dir=copied,
        record_numbers={
            role: int(record["recordNumber"]) for role, record in source_records.items()
        },
        changed_files=changed_files,
        old_values=old_values,
        new_values=new_values,
        original_hashes_before=original_before,
        original_hashes_after=original_after,
        mutated_hashes=_source_hashes(copied),
    )
    changed_names = {
        name
        for name in _SOURCE_NAMES
        if mutation.mutated_hashes[name] != original_before[name]
    }
    assert changed_names == set(changed_files)
    return copied, mutation


@pytest.fixture(scope="session")
def holdout_mutation(
    real_harness: _RealHarness,
    baseline_runs: _BaselineEvidence,
    tmp_path_factory: pytest.TempPathFactory,
) -> _LeakageEvidence:
    parent = tmp_path_factory.mktemp("sfep-real-holdout")
    data_dir, mutation = _mutated_data(
        real_harness,
        baseline_runs.runs[0],
        "HOLDOUT",
        parent / "data",
        mutate_furnace=True,
    )
    mutated = real_harness.run_producer(
        real_harness.runtimes[0], parent / "output", data_dir=data_dir
    )
    return _LeakageEvidence(baseline_runs.runs[0], mutated, mutation)


@pytest.fixture(scope="session")
def reference_mutation(
    real_harness: _RealHarness,
    baseline_runs: _BaselineEvidence,
    tmp_path_factory: pytest.TempPathFactory,
) -> _LeakageEvidence:
    parent = tmp_path_factory.mktemp("sfep-real-discovery")
    data_dir, mutation = _mutated_data(
        real_harness,
        baseline_runs.runs[0],
        "DISCOVERY",
        parent / "data",
        mutate_furnace=False,
    )
    mutated = real_harness.run_producer(
        real_harness.runtimes[1], parent / "output", data_dir=data_dir
    )
    return _LeakageEvidence(baseline_runs.runs[0], mutated, mutation)


@pytest.fixture(scope="session")
def persistent_runs(
    real_harness: _RealHarness,
    baseline_runs: _BaselineEvidence,
) -> _PersistentEvidence:
    repository_root = Path(__file__).resolve().parents[2]
    persistent_parent = repository_root / "var" / "equipment-quality"
    persistent_parent.mkdir(parents=True, exist_ok=True)

    def reserve(stem: str) -> Path:
        for index in range(10_000):
            name = stem if index == 0 else f"{stem}-{index:03d}"
            candidate = persistent_parent / name
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            return candidate
        raise AssertionError(f"cannot reserve persistent output root for {stem}")

    def promote(source: _RunEvidence, stem: str) -> _RunEvidence:
        output_root = reserve(stem)
        bundle_root = output_root / source.bundle_id
        shutil.copytree(source.bundle_root, bundle_root, copy_function=shutil.copy2)
        source_lock = source.output_root / ".sfep-equipment-quality.lock"
        assert source_lock.is_file()
        shutil.copy2(source_lock, output_root / source_lock.name)
        snapshot = real_harness._snapshot(bundle_root)
        manifest = json.loads((bundle_root / "bundle_manifest.json").read_bytes())
        return _RunEvidence(
            runtime=source.runtime,
            output_root=output_root,
            bundle_root=bundle_root,
            bundle_id=source.bundle_id,
            snapshot=snapshot,
            json_payloads={"bundle_manifest.json": manifest},
            volatile_markers=(),
        )

    def checkpoint(run: _RunEvidence) -> dict[str, object]:
        summary = json.loads((run.bundle_root / "analysis_summary.json").read_bytes())
        ranges = json.loads(
            (run.bundle_root / "equipment_operating_ranges.json").read_bytes()
        )
        rules = json.loads(
            (run.bundle_root / "quality_risk_intervals.json").read_bytes()
        )
        with (run.bundle_root / "replay_events.csv").open("rb") as stream:
            event_count = sum(block.count(b"\n") for block in iter(lambda: stream.read(1 << 20), b"")) - 1
        return {
            "splitCounts": summary["splitCounts"],
            "quarantineCounts": summary["quarantineCounts"],
            "labelCensoringCounts": summary["labelCensoringCounts"],
            "chargePurgeCounts": summary["chargePurgeCounts"],
            "holdoutMetrics": summary["holdoutMetrics"],
            "rangeCount": len(ranges["ranges"]),
            "ruleCount": len(rules["rules"]),
            "eventCount": event_count,
        }

    runs = (
        promote(baseline_runs.runs[0], "run-a"),
        promote(baseline_runs.runs[1], "run-b"),
    )
    checkpoints = (checkpoint(runs[0]), checkpoint(runs[1]))
    return _PersistentEvidence(
        baseline=baseline_runs.runs[0],
        runs=runs,
        summary_checkpoints=checkpoints,
    )
