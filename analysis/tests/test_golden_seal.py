"""Independent producer build/seal contract tests."""

from __future__ import annotations

import ast
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Callable
import warnings
import zipfile

import pytest

from factories.runtime import (
    PROVENANCE_PATH,
    independent_source_digest,
    independent_source_preimage,
    independently_expected_provenance,
    run_build_seal,
    run_producer_build,
    source_tree_bytes,
    wheel_resource,
)


ANALYSIS_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = ANALYSIS_ROOT / "tools"
WHEELHOUSE = ANALYSIS_ROOT / ".wheelhouse"
PRODUCER_FILENAME = "sfep_equipment_quality-1.0.0-py3-none-any.whl"
EXPECTED_SOURCE_DIGEST = (
    "sha256:397503b54a059b2764e1a7149d132999dbd10c0aef0128e6a3a1cb8eab250d73"
)
EXPECTED_PROVENANCE_DIGEST = (
    "sha256:76e0182250f93f53495ffd3c29e69152d9775ad590749c3e84edddff0b73a289"
)

THIRD_PARTY_WHEELS = (
    ("attrs", "26.1.0", "attrs-26.1.0-py3-none-any.whl", "py3-none-any", "c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309", "runtime"),
    ("build", "1.2.2.post1", "build-1.2.2.post1-py3-none-any.whl", "py3-none-any", "1d61c0887fa860c01971625baae8bdd338e517b836a2f70dd1f7aa3a6b2fc5b5", "build"),
    ("jsonschema", "4.24.0", "jsonschema-4.24.0-py3-none-any.whl", "py3-none-any", "a462455f19f5faf404a7902952b6f0e3ce868f3ee09a359b05eca6673bd8412d", "runtime"),
    ("jsonschema-specifications", "2025.9.1", "jsonschema_specifications-2025.9.1-py3-none-any.whl", "py3-none-any", "98802fee3a11ee76ecaca44429fda8a41bff98b00a0f2838151b113f210cc6fe", "runtime"),
    ("numpy", "2.2.6", "numpy-2.2.6-cp312-cp312-macosx_14_0_arm64.whl", "cp312-cp312-macosx_14_0_arm64", "894b3a42502226a1cac872f840030665f33326fc3dac8e57c607905773cdcde3", "runtime"),
    ("packaging", "26.3", "packaging-26.3-py3-none-any.whl", "py3-none-any", "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c", "build"),
    ("pandas", "2.3.0", "pandas-2.3.0-cp312-cp312-macosx_11_0_arm64.whl", "cp312-cp312-macosx_11_0_arm64", "b9d8c3187be7479ea5c3d30c32a5d73d62a621166675063b2edd21bc47614027", "runtime"),
    ("pip", "25.1.1", "pip-25.1.1-py3-none-any.whl", "py3-none-any", "2913a38a2abf4ea6b64ab507bd9e967f3b53dc1ede74b01b0931e1ce548751af", "bootstrap"),
    ("pyproject-hooks", "1.2.0", "pyproject_hooks-1.2.0-py3-none-any.whl", "py3-none-any", "9e5c6bfa8dcc30091c74b0cf803c81fdd29d94f01992a7707bc97babb1141913", "build"),
    ("python-dateutil", "2.9.0.post0", "python_dateutil-2.9.0.post0-py2.py3-none-any.whl", "py2.py3-none-any", "a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427", "runtime"),
    ("pytz", "2026.3.post1", "pytz-2026.3.post1-py2.py3-none-any.whl", "py2.py3-none-any", "dd95840dd199baea12d9cc096a1d452caa6596a1c1e4b5f3dbd1541855d5e815", "runtime"),
    ("referencing", "0.37.0", "referencing-0.37.0-py3-none-any.whl", "py3-none-any", "381329a9f99628c9069361716891d34ad94af76e461dcb0335825aecc7692231", "runtime"),
    ("rpds-py", "2026.6.3", "rpds_py-2026.6.3-cp312-cp312-macosx_11_0_arm64.whl", "cp312-cp312-macosx_11_0_arm64", "538949e262e46caa31ac01bdb3c1e8f642622922cacbabbae6a8445d9dc33eaf", "runtime"),
    ("setuptools", "80.9.0", "setuptools-80.9.0-py3-none-any.whl", "py3-none-any", "062d34222ad13e0cc312a4c02d73f059e86a4acbfbdea8f8f76b28c99f306922", "build"),
    ("six", "1.17.0", "six-1.17.0-py2.py3-none-any.whl", "py2.py3-none-any", "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274", "runtime"),
    ("typing-extensions", "4.16.0", "typing_extensions-4.16.0-py3-none-any.whl", "py3-none-any", "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8", "runtime"),
    ("tzdata", "2026.3", "tzdata-2026.3-py2.py3-none-any.whl", "py2.py3-none-any", "dc096730c87af6cab1b171c9d532be840741ff5d459015e7f6947bd7d7e54931", "runtime"),
    ("wheel", "0.45.1", "wheel-0.45.1-py3-none-any.whl", "py3-none-any", "708e7481cc80179af0e556bbf0cc00b8444c7321e2700b8d8580231d13017248", "build"),
)

LOCK_DIGESTS = {
    "bootstrap.lock": "9994816214185cd82bde3c2428852b6a8c77b156c54d01ec139f7f08e10832b8",
    "build-requirements.lock": "097e72ac9f7ba259d530f3a09fd10f840c58ff094761a4c17fb0c9ca8c72c4cb",
    "requirements.lock": "1fa66c13e8e7bcd8df20ead99b1adfafb227a08691fb67d8bd80861c7d8549ec",
    "wheelhouse.lock.json": "6e8ed7650891c4651586b05a561b00a85be81fd8a9ce9a264b5b710ee7b4af43",
}


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _expected_lock_bytes(group: str) -> bytes:
    selected = [item for item in THIRD_PARTY_WHEELS if item[5] == group]
    return "".join(
        f"{name}=={version} --hash=sha256:{digest}\n"
        for name, version, _filename, _tag, digest, _group in sorted(
            selected, key=lambda item: item[0].encode("utf-8")
        )
    ).encode("ascii")


def _expected_wheelhouse_lock_bytes() -> bytes:
    wheels = [
        {"filename": filename, "sha256": f"sha256:{digest}", "tag": tag}
        for _name, _version, filename, tag, digest, _group in THIRD_PARTY_WHEELS
    ]
    wheels.sort(key=lambda item: item["filename"].encode("utf-8"))
    return _canonical_json_bytes({
        "schemaVersion": "sfep-wheelhouse-lock/v1",
        "target": {
            "implementation": "CPython",
            "pythonVersion": "3.12.10",
            "system": "Darwin",
            "machine": "arm64",
        },
        "wheels": wheels,
    })


def _run_tool(tool: str, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOLS_ROOT / tool), *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _sha256_uri(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _copy_source_root(destination: Path) -> Path:
    destination.mkdir()
    shutil.copy2(ANALYSIS_ROOT / "pyproject.toml", destination / "pyproject.toml")
    shutil.copy2(ANALYSIS_ROOT / "requirements.lock", destination / "requirements.lock")
    shutil.copytree(
        ANALYSIS_ROOT / "equipment_quality",
        destination / "equipment_quality",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    (destination / "tools").mkdir()
    shutil.copy2(
        TOOLS_ROOT / "build_producer.py",
        destination / "tools/build_producer.py",
    )
    return destination


def _copy_seal_source_root(destination: Path) -> Path:
    source = _copy_source_root(destination)
    for filename in (
        "bootstrap.lock",
        "build-requirements.lock",
        "wheelhouse.lock.json",
    ):
        shutil.copy2(ANALYSIS_ROOT / filename, source / filename)
    shutil.copy2(
        TOOLS_ROOT / "seal_producer_build.py",
        source / "tools/seal_producer_build.py",
    )
    return source


def _copy_third_party_wheelhouse(destination: Path) -> Path:
    destination.mkdir()
    for _name, _version, filename, _tag, _digest, _group in THIRD_PARTY_WHEELS:
        shutil.copy2(WHEELHOUSE / filename, destination / filename)
    return destination


def _copy_wheel_pair(
    destination: Path,
    pair: tuple[Path, Path],
) -> tuple[Path, Path]:
    first = destination / "wheel-a" / PRODUCER_FILENAME
    second = destination / "wheel-b" / PRODUCER_FILENAME
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    shutil.copy2(pair[0], first)
    shutil.copy2(pair[1], second)
    return first, second


def _clone_zip_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    clone = zipfile.ZipInfo(info.orig_filename, info.date_time)
    clone.compress_type = info.compress_type
    clone.comment = info.comment
    clone.extra = info.extra
    clone.create_system = info.create_system
    clone.create_version = info.create_version
    clone.extract_version = info.extract_version
    clone.external_attr = info.external_attr
    clone.internal_attr = info.internal_attr
    clone.flag_bits = info.flag_bits
    return clone


def _new_zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, (2025, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _record_rows(payload: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload.decode("utf-8"), newline="")))


def _render_record(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _rewrite_wheel(
    wheel: Path,
    mutate: Callable[[str, bytes], tuple[str, bytes] | None],
    *,
    additions: tuple[tuple[zipfile.ZipInfo, bytes], ...] = (),
    archive_comment: bytes = b"",
    mutate_info: Callable[[zipfile.ZipInfo], None] | None = None,
) -> bytes:
    source = io.BytesIO(wheel.read_bytes())
    output = io.BytesIO()
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED
    ) as rewritten:
        for info in archive.infolist():
            changed = mutate(info.orig_filename, archive.read(info))
            if changed is None:
                continue
            name, payload = changed
            clone = _clone_zip_info(info)
            clone.filename = name
            clone.orig_filename = name
            if mutate_info is not None:
                mutate_info(clone)
            rewritten.writestr(clone, payload)
        for info, payload in additions:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                rewritten.writestr(info, payload)
        rewritten.comment = archive_comment
    return output.getvalue()


def _mutated_wheel(wheel: Path, case: str) -> bytes:
    metadata_path = "sfep_equipment_quality-1.0.0.dist-info/METADATA"
    wheel_path = "sfep_equipment_quality-1.0.0.dist-info/WHEEL"
    record_path = "sfep_equipment_quality-1.0.0.dist-info/RECORD"
    additions: tuple[tuple[zipfile.ZipInfo, bytes], ...] = ()
    archive_comment = b""
    mutate_info: Callable[[zipfile.ZipInfo], None] | None = None

    def mutate(name: str, payload: bytes) -> tuple[str, bytes] | None:
        nonlocal additions, archive_comment
        if case == "missing-provenance" and name == PROVENANCE_PATH:
            return None
        if case == "metadata-name" and name == metadata_path:
            return name, payload.replace(
                b"Name: sfep-equipment-quality", b"Name: other-producer"
            )
        if case == "metadata-version" and name == metadata_path:
            return name, payload.replace(b"Version: 1.0.0", b"Version: 9.0.0")
        if case == "wheel-tag" and name == wheel_path:
            return name, payload.replace(b"Tag: py3-none-any", b"Tag: cp312-none-any")
        if case.startswith("provenance-") and name == PROVENANCE_PATH:
            value = json.loads(payload)
            if case == "provenance-extra":
                value["unexpected"] = True
                return name, _canonical_json_bytes(value)
            if case == "provenance-source":
                value["sourceSha256"] = "sha256:" + "0" * 64
                return name, _canonical_json_bytes(value)
            if case == "provenance-noncanonical":
                return name, (json.dumps(value, indent=2) + "\n").encode()
        if name == record_path and case.startswith("record-"):
            rows = _record_rows(payload)
            if case == "record-hash":
                rows[0][1] = "sha256=" + base64.urlsafe_b64encode(b"\0" * 32).rstrip(b"=").decode()
            elif case == "record-size":
                rows[0][2] = str(int(rows[0][2]) + 1)
            elif case == "record-self":
                rows[-1][1] = "sha256=" + "A" * 43
            elif case == "record-duplicate":
                rows.insert(1, list(rows[0]))
            elif case == "record-missing":
                rows.pop(0)
            elif case == "record-algorithm":
                rows[0][1] = "md5=" + "A" * 43
            return name, _render_record(rows)
        return name, payload

    if case == "duplicate-member":
        with zipfile.ZipFile(wheel) as archive:
            first = archive.infolist()[0]
            additions = ((_clone_zip_info(first), archive.read(first)),)
    elif case == "traversal-member":
        additions = ((_new_zip_info("../escape.py"), b"escape"),)
    elif case == "absolute-member":
        additions = ((_new_zip_info("/escape.py"), b"escape"),)
    elif case == "backslash-member":
        additions = ((_new_zip_info("equipment_quality\\escape.py"), b"escape"),)
    elif case == "control-member":
        additions = ((_new_zip_info("equipment_quality/bad\x01.py"), b"escape"),)
    elif case == "directory-member":
        info = _new_zip_info("equipment_quality/extra/")
        info.external_attr = (stat.S_IFDIR | 0o755) << 16
        additions = ((info, b""),)
    elif case == "symlink-member":
        info = _new_zip_info("equipment_quality/link.py")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        additions = ((info, b"equipment_quality/__init__.py"),)
    elif case == "unexpected-source":
        additions = ((_new_zip_info("equipment_quality/unexpected.py"), b"bad"),)
    elif case == "timestamp":
        def change_timestamp(info: zipfile.ZipInfo) -> None:
            if info.filename == "equipment_quality/__init__.py":
                info.date_time = (2025, 1, 2, 0, 0, 0)

        mutate_info = change_timestamp
    elif case == "mode":
        def change_mode(info: zipfile.ZipInfo) -> None:
            if info.filename == "equipment_quality/__init__.py":
                info.external_attr = (stat.S_IFREG | 0o755) << 16

        mutate_info = change_mode
    elif case == "archive-comment":
        archive_comment = b"unexpected"
    return _rewrite_wheel(
        wheel,
        mutate,
        additions=additions,
        archive_comment=archive_comment,
        mutate_info=mutate_info,
    )


@pytest.fixture(scope="module")
def build_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    environment_root = tmp_path_factory.mktemp("producer-build-python") / "venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", "--copies", str(environment_root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    python = environment_root / "bin/python"
    assert python.is_file()
    assert not python.is_symlink()
    offline = [
        str(python),
        "-m",
        "pip",
        "install",
        "--no-index",
        "--only-binary=:all:",
        "--require-hashes",
        "--find-links",
        str(WHEELHOUSE),
    ]
    for lock in (ANALYSIS_ROOT / "bootstrap.lock", ANALYSIS_ROOT / "build-requirements.lock"):
        installed = subprocess.run(
            [*offline, "-r", str(lock)],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
        )
        assert installed.returncode == 0, installed.stderr
    return python


def test_producer_tools_offer_exact_cli_help(tmp_path: Path) -> None:
    build = _run_tool("build_producer.py", "--help", cwd=tmp_path)
    seal = _run_tool("seal_producer_build.py", "--help", cwd=tmp_path)

    assert build.returncode == 0, build.stderr
    assert "--source-root" in build.stdout
    assert "--build-python" in build.stdout
    assert "--work-root" in build.stdout
    assert seal.returncode == 0, seal.stderr
    assert "--source-root" in seal.stdout
    assert "--wheel-dir-a" in seal.stdout
    assert "--wheel-dir-b" in seal.stdout
    assert "--wheelhouse" in seal.stdout
    assert "--producer-lock" in seal.stdout


def test_frozen_third_party_locks_match_independent_literal_oracle() -> None:
    expected = {
        "bootstrap.lock": _expected_lock_bytes("bootstrap"),
        "build-requirements.lock": _expected_lock_bytes("build"),
        "requirements.lock": _expected_lock_bytes("runtime"),
        "wheelhouse.lock.json": _expected_wheelhouse_lock_bytes(),
    }
    assert len(THIRD_PARTY_WHEELS) == 18
    inventory = {path.name for path in WHEELHOUSE.iterdir()}
    expected_third_party = {item[2] for item in THIRD_PARTY_WHEELS}
    assert inventory - {PRODUCER_FILENAME} == expected_third_party
    assert inventory <= expected_third_party | {PRODUCER_FILENAME}
    for _name, _version, filename, _tag, digest, _group in THIRD_PARTY_WHEELS:
        assert hashlib.sha256((WHEELHOUSE / filename).read_bytes()).hexdigest() == digest
    for filename, payload in expected.items():
        assert (ANALYSIS_ROOT / filename).read_bytes() == payload
        assert hashlib.sha256(payload).hexdigest() == LOCK_DIGESTS[filename]
    if PRODUCER_FILENAME in inventory:
        producer_digest = hashlib.sha256(
            (WHEELHOUSE / PRODUCER_FILENAME).read_bytes()
        ).hexdigest()
        assert (ANALYSIS_ROOT / "producer.lock").read_bytes() == (
            "sfep-equipment-quality==1.0.0 "
            f"--hash=sha256:{producer_digest}\n"
        ).encode("ascii")


def test_source_preimage_and_provenance_match_independent_literals() -> None:
    preimage = independent_source_preimage(ANALYSIS_ROOT)
    provenance = independently_expected_provenance(ANALYSIS_ROOT)

    assert len(source_tree_bytes(ANALYSIS_ROOT)) == 25
    assert preimage.startswith(b"sfep-source-lines/v1\n")
    assert independent_source_digest(ANALYSIS_ROOT) == EXPECTED_SOURCE_DIGEST
    assert _sha256_uri(provenance) == EXPECTED_PROVENANCE_DIGEST
    assert len(provenance) == 364


def test_producer_tools_import_only_the_standard_library() -> None:
    for filename in ("build_producer.py", "seal_producer_build.py"):
        tree = ast.parse((TOOLS_ROOT / filename).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.partition(".")[0])
        assert "equipment_quality" not in imported
        assert imported <= sys.stdlib_module_names


def test_build_stages_one_snapshot_twice_without_mutating_source(
    tmp_path: Path,
    build_python: Path,
) -> None:
    before = source_tree_bytes(ANALYSIS_ROOT)
    first_work = tmp_path / "first-absolute-work-root"
    second_work = tmp_path / "second-absolute-work-root"

    first = run_producer_build(
        ANALYSIS_ROOT,
        build_python,
        first_work,
        cwd=tmp_path,
    )
    second = run_producer_build(
        ANALYSIS_ROOT,
        build_python,
        second_work,
        cwd=tmp_path,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    wheels = [
        root / directory / PRODUCER_FILENAME
        for root in (first_work, second_work)
        for directory in ("wheel-a", "wheel-b")
    ]
    assert all(wheel.is_file() for wheel in wheels)
    assert len({wheel.read_bytes() for wheel in wheels}) == 1
    assert source_tree_bytes(ANALYSIS_ROOT) == before
    assert not (ANALYSIS_ROOT / PROVENANCE_PATH).exists()
    expected_provenance = independently_expected_provenance(ANALYSIS_ROOT)
    assert wheel_resource(wheels[0], PROVENANCE_PATH) == expected_provenance

    forbidden = {
        str(ANALYSIS_ROOT).encode(),
        str(first_work).encode(),
        str(second_work).encode(),
    }
    with zipfile.ZipFile(wheels[0]) as archive:
        assert archive.infolist()
        for info in archive.infolist():
            assert info.date_time == (2025, 1, 1, 0, 0, 0)
            payload = archive.read(info)
            assert all(token not in payload for token in forbidden)
            mode = info.external_attr >> 16
            assert stat.S_IFMT(mode) in {0, stat.S_IFREG}
            expected_mode = 0o664 if info.filename.endswith(".dist-info/RECORD") else 0o644
            assert stat.S_IMODE(mode) == expected_mode


def test_source_mutation_changes_provenance_not_the_source_tree(
    tmp_path: Path,
    build_python: Path,
) -> None:
    source = _copy_source_root(tmp_path / "mutated-analysis")
    target = source / "equipment_quality/__init__.py"
    target.write_bytes(target.read_bytes() + b"# test-side source mutation\n")
    before = source_tree_bytes(source)
    expected = independently_expected_provenance(source)

    result = run_producer_build(source, build_python, tmp_path / "mutated-work")

    assert result.returncode == 0, result.stderr
    wheel = tmp_path / "mutated-work/wheel-a" / PRODUCER_FILENAME
    assert wheel_resource(wheel, PROVENANCE_PATH) == expected
    assert json.loads(expected)["sourceSha256"] != EXPECTED_SOURCE_DIGEST
    assert source_tree_bytes(source) == before
    assert not (source / PROVENANCE_PATH).exists()


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("relative-source", "absolute"),
        ("symlink-source", "source root"),
        ("symlink-python", "build Python"),
        ("nonempty-work", "empty"),
        ("source-overlap", "overlap"),
        ("source-symlink-entry", "symlink"),
        ("source-provenance", "provenance"),
    ),
)
def test_build_rejects_unsafe_inputs_without_claimed_wheel_pair(
    tmp_path: Path,
    build_python: Path,
    case: str,
    message: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    requested_source: Path | str = source
    requested_python = build_python
    work_root = tmp_path / "work"
    if case == "relative-source":
        requested_source = Path("analysis-copy")
    elif case == "symlink-source":
        link = tmp_path / "source-link"
        link.symlink_to(source, target_is_directory=True)
        requested_source = link
    elif case == "symlink-python":
        link = tmp_path / "python-link"
        link.symlink_to(build_python)
        requested_python = link
    elif case == "nonempty-work":
        work_root.mkdir()
        (work_root / "occupied").write_bytes(b"occupied")
    elif case == "source-overlap":
        work_root = source / "work"
    elif case == "source-symlink-entry":
        (source / "equipment_quality/link.py").symlink_to(
            source / "equipment_quality/__init__.py"
        )
    elif case == "source-provenance":
        (source / PROVENANCE_PATH).write_bytes(b"stale provenance\n")

    result = run_producer_build(
        Path(requested_source),
        requested_python,
        work_root,
        cwd=tmp_path,
    )

    assert result.returncode != 0
    assert message.casefold() in result.stderr.casefold()
    assert not (work_root / "wheel-a" / PRODUCER_FILENAME).exists()
    assert not (work_root / "wheel-b" / PRODUCER_FILENAME).exists()


@pytest.fixture(scope="module")
def producer_wheel_pair(
    tmp_path_factory: pytest.TempPathFactory,
    build_python: Path,
) -> tuple[Path, Path]:
    work_root = tmp_path_factory.mktemp("producer-wheel-pair") / "work"
    result = run_producer_build(ANALYSIS_ROOT, build_python, work_root)
    assert result.returncode == 0, result.stderr
    return (
        work_root / "wheel-a" / PRODUCER_FILENAME,
        work_root / "wheel-b" / PRODUCER_FILENAME,
    )


def _expected_producer_lock(wheel: Path) -> bytes:
    return (
        "sfep-equipment-quality==1.0.0 "
        f"--hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}\n"
    ).encode("ascii")


def test_build_seal_publishes_only_an_authenticated_identical_pair(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    producer_lock = tmp_path / "producer.lock"

    first = run_build_seal(
        ANALYSIS_ROOT,
        wheel_a.parent,
        wheel_b.parent,
        wheelhouse,
        producer_lock,
        cwd=tmp_path,
    )
    second = run_build_seal(
        ANALYSIS_ROOT,
        wheel_a.parent,
        wheel_b.parent,
        wheelhouse,
        producer_lock,
        cwd=tmp_path,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    published = wheelhouse / PRODUCER_FILENAME
    assert published.read_bytes() == wheel_a.read_bytes()
    assert producer_lock.read_bytes() == _expected_producer_lock(wheel_a)
    assert len(producer_lock.read_bytes()) == 109
    lock_value = json.loads((ANALYSIS_ROOT / "wheelhouse.lock.json").read_bytes())
    assert all(
        entry["filename"] != PRODUCER_FILENAME for entry in lock_value["wheels"]
    )


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("malformed", "zip"),
        ("duplicate-member", "duplicate"),
        ("traversal-member", "unsafe"),
        ("absolute-member", "unsafe"),
        ("backslash-member", "unsafe"),
        ("control-member", "unsafe"),
        ("directory-member", "directory"),
        ("symlink-member", "mode"),
        ("unexpected-source", "inventory"),
        ("timestamp", "timestamp"),
        ("mode", "mode"),
        ("archive-comment", "comment"),
        ("metadata-name", "metadata"),
        ("metadata-version", "metadata"),
        ("wheel-tag", "wheel metadata"),
        ("missing-provenance", "provenance"),
        ("provenance-extra", "provenance"),
        ("provenance-source", "provenance"),
        ("provenance-noncanonical", "provenance"),
        ("record-hash", "record"),
        ("record-size", "record"),
        ("record-self", "record"),
        ("record-duplicate", "record"),
        ("record-missing", "record"),
        ("record-algorithm", "record"),
    ),
)
def test_build_seal_rejects_wheel_archive_mutations_without_publication(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    case: str,
    message: str,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    payload = b"not a zip archive\n" if case == "malformed" else _mutated_wheel(wheel_a, case)
    wheel_a.write_bytes(payload)
    wheel_b.write_bytes(payload)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    producer_lock = tmp_path / "producer.lock"

    result = run_build_seal(
        ANALYSIS_ROOT,
        wheel_a.parent,
        wheel_b.parent,
        wheelhouse,
        producer_lock,
    )

    assert result.returncode != 0
    assert message.casefold() in result.stderr.casefold()
    assert not producer_lock.exists()
    assert not (wheelhouse / PRODUCER_FILENAME).exists()


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("different", "differ"),
        ("missing", "exactly one"),
        ("extra", "exactly one"),
        ("wrong-name", "expected"),
        ("symlink", "regular"),
    ),
)
def test_build_seal_requires_two_exact_wheel_directories(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    case: str,
    message: str,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    if case == "different":
        wheel_b.write_bytes(wheel_b.read_bytes() + b"different")
    elif case == "missing":
        wheel_b.unlink()
    elif case == "extra":
        (wheel_b.parent / "extra.whl").write_bytes(b"extra")
    elif case == "wrong-name":
        wheel_b.rename(wheel_b.with_name("wrong-1.0.0-py3-none-any.whl"))
    elif case == "symlink":
        target = tmp_path / "producer-wheel"
        target.write_bytes(wheel_b.read_bytes())
        wheel_b.unlink()
        wheel_b.symlink_to(target)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    producer_lock = tmp_path / "producer.lock"

    result = run_build_seal(
        ANALYSIS_ROOT,
        wheel_a.parent,
        wheel_b.parent,
        wheelhouse,
        producer_lock,
    )

    assert result.returncode != 0
    assert message.casefold() in result.stderr.casefold()
    assert not producer_lock.exists()
    assert not (wheelhouse / PRODUCER_FILENAME).exists()


@pytest.mark.parametrize("case", ("same-directory", "hardlinked-wheel"))
def test_build_seal_requires_physically_independent_wheel_inputs(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    case: str,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheel_dir_b = wheel_b.parent
    if case == "same-directory":
        wheel_dir_b = wheel_a.parent
    else:
        wheel_b.unlink()
        wheel_b.hardlink_to(wheel_a)
        wheel_a_identity = os.lstat(wheel_a)
        wheel_b_identity = os.lstat(wheel_b)
        assert (wheel_a_identity.st_dev, wheel_a_identity.st_ino) == (
            wheel_b_identity.st_dev,
            wheel_b_identity.st_ino,
        )
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    producer_lock = tmp_path / "producer.lock"

    result = run_build_seal(
        ANALYSIS_ROOT,
        wheel_a.parent,
        wheel_dir_b,
        wheelhouse,
        producer_lock,
    )

    assert result.returncode != 0
    assert "independent" in result.stderr.casefold()
    assert not producer_lock.exists()
    assert not (wheelhouse / PRODUCER_FILENAME).exists()


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("missing-wheel", "inventory"),
        ("extra-sdist", "inventory"),
        ("extra-wheel", "inventory"),
        ("wheel-hash", "digest"),
        ("wheel-symlink", "symlink"),
        ("lock-tag", "wheelhouse lock"),
        ("requirements", "requirements.lock"),
        ("source", "source"),
    ),
)
def test_build_seal_rejects_source_lock_and_wheelhouse_drift(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    case: str,
    message: str,
) -> None:
    source = _copy_seal_source_root(tmp_path / "analysis-copy")
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    if case == "missing-wheel":
        (wheelhouse / THIRD_PARTY_WHEELS[0][2]).unlink()
    elif case == "extra-sdist":
        (wheelhouse / "attrs-26.1.0.tar.gz").write_bytes(b"sdist")
    elif case == "extra-wheel":
        (wheelhouse / "other-1.0.0-py3-none-any.whl").write_bytes(b"wheel")
    elif case == "wheel-hash":
        target = wheelhouse / THIRD_PARTY_WHEELS[0][2]
        target.write_bytes(target.read_bytes() + b"drift")
    elif case == "wheel-symlink":
        target = wheelhouse / THIRD_PARTY_WHEELS[0][2]
        payload = tmp_path / "attrs-wheel"
        payload.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(payload)
    elif case == "lock-tag":
        lock_path = source / "wheelhouse.lock.json"
        lock = json.loads(lock_path.read_bytes())
        lock["wheels"][0]["tag"] = "cp312-none-any"
        lock_path.write_bytes(_canonical_json_bytes(lock))
    elif case == "requirements":
        lock_path = source / "requirements.lock"
        lock_path.write_bytes(
            lock_path.read_bytes().replace(
                THIRD_PARTY_WHEELS[0][4].encode(), b"0" * 64, 1
            )
        )
    elif case == "source":
        target = source / "equipment_quality/__init__.py"
        target.write_bytes(target.read_bytes() + b"# drift\n")
    producer_lock = tmp_path / "producer.lock"

    result = run_build_seal(
        source,
        wheel_a.parent,
        wheel_b.parent,
        wheelhouse,
        producer_lock,
    )

    assert result.returncode != 0
    assert message.casefold() in result.stderr.casefold()
    assert not producer_lock.exists()
    assert not (wheelhouse / PRODUCER_FILENAME).exists()


@pytest.mark.parametrize("case", ("lock-only", "wheel-only", "both-different"))
def test_build_seal_refuses_stale_outputs_without_overwrite(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    case: str,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    producer_lock = tmp_path / "producer.lock"
    published = wheelhouse / PRODUCER_FILENAME
    if case in {"lock-only", "both-different"}:
        producer_lock.write_bytes(b"stale lock\n")
    if case in {"wheel-only", "both-different"}:
        published.write_bytes(b"stale wheel\n")
    lock_before = producer_lock.read_bytes() if producer_lock.exists() else None
    wheel_before = published.read_bytes() if published.exists() else None

    result = run_build_seal(
        ANALYSIS_ROOT,
        wheel_a.parent,
        wheel_b.parent,
        wheelhouse,
        producer_lock,
    )

    assert result.returncode != 0
    assert "existing producer" in result.stderr.casefold()
    assert (producer_lock.read_bytes() if producer_lock.exists() else None) == lock_before
    assert (published.read_bytes() if published.exists() else None) == wheel_before
