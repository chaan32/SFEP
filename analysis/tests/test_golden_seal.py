"""Independent producer build/seal contract tests."""

from __future__ import annotations

import ast
import base64
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Callable
import warnings
import zipfile

import pytest

from factories.runtime import (
    PROVENANCE_PATH,
    independent_source_digest,
    independent_source_preimage,
    independently_expected_provenance,
    prepare_producer_work_root,
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
    "sha256:3e7fbecbec0d5312aa89de7429b1893f3f791582e9366997ed640984e7305d9c"
)
EXPECTED_PROVENANCE_DIGEST = (
    "sha256:60bb3912aac62515d9ac8895f63cbc0f6623b86fd8b2f9250c21b3c8adbfc4ae"
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
    if case.startswith(("local-", "eocd-", "central-")) or case == "unclaimed-gap":
        return _mutated_raw_zip(wheel, case)
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


def _mutated_raw_zip(wheel: Path, case: str) -> bytes:
    payload = bytearray(wheel.read_bytes())
    assert payload[:4] == b"PK\x03\x04"
    if case == "local-timestamp":
        struct.pack_into("<H", payload, 12, 0)
    elif case == "local-method":
        struct.pack_into("<H", payload, 8, zipfile.ZIP_STORED)
    elif case == "local-crc":
        crc = struct.unpack_from("<I", payload, 14)[0]
        struct.pack_into("<I", payload, 14, crc ^ 1)
    elif case == "local-compressed-size":
        size = struct.unpack_from("<I", payload, 18)[0]
        struct.pack_into("<I", payload, 18, size + 1)
    elif case == "local-uncompressed-size":
        size = struct.unpack_from("<I", payload, 22)[0]
        struct.pack_into("<I", payload, 22, size + 1)
    elif case == "local-data-descriptor":
        flags = struct.unpack_from("<H", payload, 6)[0]
        struct.pack_into("<H", payload, 6, flags | 0x0008)
    elif case == "local-encryption":
        flags = struct.unpack_from("<H", payload, 6)[0]
        struct.pack_into("<H", payload, 6, flags | 0x0001)
    elif case == "unclaimed-gap":
        eocd_offset = len(payload) - 22
        assert payload[eocd_offset:eocd_offset + 4] == b"PK\x05\x06"
        central_offset = struct.unpack_from("<I", payload, eocd_offset + 16)[0]
        gap = b"unclaimed"
        payload[central_offset:central_offset] = gap
        struct.pack_into(
            "<I",
            payload,
            eocd_offset + len(gap) + 16,
            central_offset + len(gap),
        )
    elif case == "eocd-disk":
        struct.pack_into("<H", payload, len(payload) - 22 + 4, 1)
    elif case == "eocd-central-size":
        eocd_offset = len(payload) - 22
        central_size = struct.unpack_from("<I", payload, eocd_offset + 12)[0]
        struct.pack_into("<I", payload, eocd_offset + 12, central_size + 1)
    elif case == "central-needed-version":
        central_offset = struct.unpack_from("<I", payload, len(payload) - 22 + 16)[0]
        struct.pack_into("<H", payload, 4, 21)
        struct.pack_into("<H", payload, central_offset + 6, 21)
    elif case == "central-internal-attribute":
        central_offset = struct.unpack_from("<I", payload, len(payload) - 22 + 16)[0]
        struct.pack_into("<H", payload, central_offset + 36, 1)
    elif case == "central-external-low-bits":
        central_offset = struct.unpack_from("<I", payload, len(payload) - 22 + 16)[0]
        attributes = struct.unpack_from("<I", payload, central_offset + 38)[0]
        struct.pack_into("<I", payload, central_offset + 38, attributes | 1)
    else:
        raise AssertionError(f"unknown raw ZIP mutation: {case}")
    return bytes(payload)


def _zip_with_false_end_signature_in_compressed_data() -> bytes:
    data = bytearray(b"A" * 80)
    data[8:12] = b"PK\x05\x06"

    def render() -> bytes:
        output = io.BytesIO()
        info = _new_zip_info("x")
        with zipfile.ZipFile(
            output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=0,
        ) as archive:
            archive.writestr(
                info,
                bytes(data),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=0,
            )
        return output.getvalue()

    payload = render()
    false_offset = payload.index(b"PK\x05\x06")
    assert false_offset != len(payload) - 22
    pseudo_comment_size = len(payload) - false_offset - 22
    data[28:30] = struct.pack("<H", pseudo_comment_size)
    payload = render()
    assert struct.unpack_from("<H", payload, false_offset + 20)[0] == (
        len(payload) - false_offset - 22
    )
    return payload


def _zip_with_declared_unused_deflate_byte() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(_new_zip_info("x"), b"authenticated payload")
    payload = bytearray(output.getvalue())
    original_end_offset = len(payload) - 22
    original_central_offset = struct.unpack_from(
        "<I", payload, original_end_offset + 16
    )[0]
    filename_size = struct.unpack_from("<H", payload, 26)[0]
    extra_size = struct.unpack_from("<H", payload, 28)[0]
    compressed_size = struct.unpack_from("<I", payload, 18)[0]
    data_end = 30 + filename_size + extra_size + compressed_size
    assert data_end == original_central_offset

    payload[data_end:data_end] = b"\x00"
    shifted_central_offset = original_central_offset + 1
    shifted_end_offset = original_end_offset + 1
    struct.pack_into("<I", payload, 18, compressed_size + 1)
    struct.pack_into(
        "<I", payload, shifted_central_offset + 20, compressed_size + 1
    )
    struct.pack_into(
        "<I", payload, shifted_end_offset + 16, shifted_central_offset
    )
    return bytes(payload)


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


def test_raw_zip_layout_uses_the_exact_tail_end_record() -> None:
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    payload = _zip_with_false_end_signature_in_compressed_data()

    records = namespace["_raw_zip_layout"](payload)

    assert len(records) == 1
    assert records[0]["filename"] == b"x"


def test_raw_zip_layout_rejects_declared_bytes_after_a_deflate_stream() -> None:
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    error_type = namespace["ProducerSealError"]
    payload = _zip_with_declared_unused_deflate_byte()

    with pytest.raises(error_type, match="DEFLATE|compressed|stream"):
        namespace["_raw_zip_layout"](payload)


def test_build_stages_one_snapshot_twice_without_mutating_source(
    tmp_path: Path,
    build_python: Path,
) -> None:
    before = source_tree_bytes(ANALYSIS_ROOT)
    first_work = prepare_producer_work_root(
        tmp_path / "first-absolute-work-root"
    )
    second_work = prepare_producer_work_root(
        tmp_path / "second-absolute-work-root"
    )

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


def _caller_surface(root: Path) -> dict[str, tuple[object, ...]]:
    """Capture caller-owned names without following symlinks."""
    if not os.path.lexists(root):
        return {}
    captured: dict[str, tuple[object, ...]] = {}
    pending = [(root, ".")]
    while pending:
        current, relative = pending.pop()
        result = os.lstat(current)
        kind = stat.S_IFMT(result.st_mode)
        detail: object = None
        if stat.S_ISLNK(result.st_mode):
            detail = os.readlink(current)
        elif stat.S_ISREG(result.st_mode):
            detail = current.read_bytes()
        captured[relative] = (
            result.st_dev,
            result.st_ino,
            kind,
            stat.S_IMODE(result.st_mode),
            detail,
        )
        if stat.S_ISDIR(result.st_mode):
            pending.extend(
                (Path(entry.path), f"{relative}/{entry.name}")
                for entry in os.scandir(current)
            )
    return captured


def _injected_base_exception(kind: str, label: str) -> BaseException:
    if kind == "keyboard-interrupt":
        return KeyboardInterrupt(label)
    if kind == "system-exit":
        return SystemExit(label)
    raise AssertionError(f"unsupported BaseException kind: {kind}")


def _current_umask() -> int:
    current = os.umask(0o777)
    os.umask(current)
    return current


@pytest.mark.parametrize(
    "case",
    (
        "missing-root",
        "missing-output-directories",
        "missing-wheel-b",
        "extra-root-entry",
        "symlink-wheel-a",
        "nonempty-wheel-a",
        "nonempty-wheel-b",
        "wheel-a-file",
    ),
)
def test_build_requires_an_exact_prepared_root_without_mutating_caller_data(
    tmp_path: Path,
    build_python: Path,
    case: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = tmp_path / "work"
    if case not in {
        "missing-root",
        "nonempty-wheel-a",
        "nonempty-wheel-b",
    }:
        work_root.mkdir()
    if case == "missing-wheel-b":
        (work_root / "wheel-a").mkdir()
    elif case == "extra-root-entry":
        (work_root / "wheel-a").mkdir()
        (work_root / "wheel-b").mkdir()
        (work_root / "caller.txt").write_bytes(b"caller-owned")
    elif case == "symlink-wheel-a":
        external = tmp_path / "external-wheel-a"
        external.mkdir()
        (work_root / "wheel-a").symlink_to(external, target_is_directory=True)
        (work_root / "wheel-b").mkdir()
    elif case == "nonempty-wheel-a":
        prepare_producer_work_root(work_root)
        (work_root / "wheel-a/caller.whl").write_bytes(b"caller-owned")
    elif case == "nonempty-wheel-b":
        prepare_producer_work_root(work_root)
        (work_root / "wheel-b/caller.whl").write_bytes(b"caller-owned")
    elif case == "wheel-a-file":
        (work_root / "wheel-a").write_bytes(b"caller-owned")
        (work_root / "wheel-b").mkdir()
    before = _caller_surface(work_root)
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"

    with pytest.raises((error_type, OSError)):
        module_globals["_run"](source, build_python, work_root)

    assert _caller_surface(work_root) == before


def test_build_uses_prepared_directories_without_changing_their_identity_or_mode(
    tmp_path: Path,
    build_python: Path,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    caller_directories = (
        work_root,
        work_root / "wheel-a",
        work_root / "wheel-b",
    )
    before = {
        path: (
            os.lstat(path).st_dev,
            os.lstat(path).st_ino,
            stat.S_IMODE(os.lstat(path).st_mode),
        )
        for path in caller_directories
    }
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"

    previous_umask = os.umask(0o777)
    try:
        result = module_globals["_run"](source, build_python, work_root)
    finally:
        os.umask(previous_umask)

    assert result[2] == len(b"identical test wheel")
    for path in caller_directories:
        current = os.lstat(path)
        assert (current.st_dev, current.st_ino, stat.S_IMODE(current.st_mode)) == before[path]
    for directory_name in ("wheel-a", "wheel-b"):
        output = work_root / directory_name / PRODUCER_FILENAME
        assert output.read_bytes() == b"identical test wheel"
        assert stat.S_IMODE(os.lstat(output).st_mode) == 0o644


def test_build_pins_the_root_and_both_output_directories_before_source_snapshot(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    real_open = os.open
    real_snapshot = module_globals["_source_snapshot"]
    retained: dict[str, int] = {}

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == work_root:
            retained["work"] = descriptor
        elif path in {"wheel-a", "wheel-b"}:
            retained[str(path)] = descriptor
        return descriptor

    def asserting_snapshot(path: Path) -> dict[str, bytes]:
        assert set(retained) == {"work", "wheel-a", "wheel-b"}
        assert all(stat.S_ISDIR(os.fstat(value).st_mode) for value in retained.values())
        return real_snapshot(path)

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    module_globals["_source_snapshot"] = asserting_snapshot

    module_globals["_run"](source, build_python, work_root)

    assert all(
        (work_root / name / PRODUCER_FILENAME).is_file()
        for name in ("wheel-a", "wheel-b")
    )


def test_build_treats_replacement_before_initial_output_open_as_caller_input(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    detached = tmp_path / "wheel-a-before-open"
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    real_open = os.open
    replaced = False

    def replacing_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal replaced
        if path == "wheel-a" and not replaced:
            (work_root / "wheel-a").rename(detached)
            (work_root / "wheel-a").mkdir()
            replaced = True
        return real_open(path, flags, *arguments, **keywords)

    monkeypatch.setattr(module_globals["os"], "open", replacing_open)

    module_globals["_run"](source, build_python, work_root)

    assert replaced
    assert list(detached.iterdir()) == []
    assert (work_root / "wheel-a" / PRODUCER_FILENAME).is_file()


def test_build_preserves_a_replacement_after_pin_and_cleans_the_detached_owned_file(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    detached = tmp_path / "wheel-a-after-pin"
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    real_open = os.open
    replacement_identity: tuple[int, int] | None = None

    def replacing_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal replacement_identity
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == PRODUCER_FILENAME and replacement_identity is None:
            (work_root / "wheel-a").rename(detached)
            (work_root / "wheel-a").mkdir()
            current = os.lstat(work_root / "wheel-a")
            replacement_identity = current.st_dev, current.st_ino
        return descriptor

    monkeypatch.setattr(module_globals["os"], "open", replacing_open)

    with pytest.raises(error_type, match="changed|identity|inventory"):
        module_globals["_run"](source, build_python, work_root)

    assert replacement_identity is not None
    current = os.lstat(work_root / "wheel-a")
    assert (current.st_dev, current.st_ino) == replacement_identity
    assert list((work_root / "wheel-a").iterdir()) == []
    detached_scratch = detached / PRODUCER_FILENAME
    assert detached_scratch.read_bytes() == b""
    assert list((work_root / "wheel-b").iterdir()) == []


def test_build_invalidates_the_owned_fd_despite_persistent_output_name_stat_failure(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    identities = {
        path: (os.lstat(path).st_dev, os.lstat(path).st_ino)
        for path in (work_root / "wheel-a", work_root / "wheel-b")
    }
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    real_open = os.open
    real_stat = os.stat
    output_opens = 0
    stat_broken = False

    def failing_second_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal output_opens, stat_broken
        if path == PRODUCER_FILENAME:
            output_opens += 1
            if output_opens == 2:
                stat_broken = True
                raise PermissionError("injected second output open failure")
        return real_open(path, flags, *arguments, **keywords)

    def failing_stat(
        path: os.PathLike[str] | str | bytes | int,
        *arguments: object,
        **keywords: object,
    ) -> os.stat_result:
        if stat_broken and path == PRODUCER_FILENAME:
            raise OSError("persistent output-name stat failure")
        return real_stat(path, *arguments, **keywords)

    monkeypatch.setattr(module_globals["os"], "open", failing_second_open)
    monkeypatch.setattr(module_globals["os"], "stat", failing_stat)

    with pytest.raises(
        (PermissionError, module_globals["ProducerBuildError"]),
        match="second output|cannot be created",
    ):
        module_globals["_run"](source, build_python, work_root)

    assert output_opens == 2
    for path, identity in identities.items():
        current = os.lstat(path)
        assert (current.st_dev, current.st_ino) == identity
    assert (work_root / "wheel-a" / PRODUCER_FILENAME).read_bytes() == b""
    assert list((work_root / "wheel-b").iterdir()) == []


def test_build_cleanup_never_unlinks_a_replacement_installed_after_identity_check(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    target = work_root / "wheel-a" / PRODUCER_FILENAME
    detached_owned = tmp_path / "detached-owned-wheel"
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    real_open = os.open
    real_stat = os.stat
    real_ftruncate = os.ftruncate
    output_opens = 0
    cleanup_active = False
    replaced = False

    def install_replacement() -> None:
        nonlocal replaced
        if replaced:
            return
        target.rename(detached_owned)
        target.write_bytes(b"external replacement")
        replaced = True

    def failing_second_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal output_opens, cleanup_active
        if path == PRODUCER_FILENAME:
            output_opens += 1
            if output_opens == 2:
                cleanup_active = True
                raise PermissionError("injected second output open failure")
        return real_open(path, flags, *arguments, **keywords)

    def replacing_after_stat(
        path: os.PathLike[str] | str | bytes | int,
        *arguments: object,
        **keywords: object,
    ) -> os.stat_result:
        nonlocal replaced
        result = real_stat(path, *arguments, **keywords)
        if cleanup_active and path == PRODUCER_FILENAME and not replaced:
            install_replacement()
        return result

    def replacing_before_ftruncate(descriptor: int, length: int) -> None:
        if cleanup_active and not replaced:
            install_replacement()
        real_ftruncate(descriptor, length)

    monkeypatch.setattr(module_globals["os"], "open", failing_second_open)
    monkeypatch.setattr(module_globals["os"], "stat", replacing_after_stat)
    monkeypatch.setattr(
        module_globals["os"],
        "ftruncate",
        replacing_before_ftruncate,
    )

    with pytest.raises(
        (PermissionError, module_globals["ProducerBuildError"]),
        match="cannot be created|second output",
    ):
        module_globals["_run"](source, build_python, work_root)

    assert replaced
    assert target.read_bytes() == b"external replacement"
    assert detached_owned.read_bytes() == b""
    assert list((work_root / "wheel-b").iterdir()) == []


@pytest.mark.parametrize("exception_kind", ("keyboard-interrupt", "system-exit"))
@pytest.mark.parametrize(
    "phase",
    (
        "after-open-a",
        "after-open-b",
        "during-write",
        "after-write",
        "between-final-checks",
    ),
)
def test_build_publication_base_exception_invalidates_scratch_and_reraises_original(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception_kind: str,
    phase: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    original = _injected_base_exception(exception_kind, phase)
    real_open = os.open
    real_fstat = os.fstat
    real_write = os.write
    real_fsync = os.fsync
    work_descriptor: int | None = None
    output_descriptors: list[int] = []
    injected = False

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal work_descriptor
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == work_root:
            work_descriptor = descriptor
        elif path == PRODUCER_FILENAME:
            output_descriptors.append(descriptor)
        return descriptor

    def interrupting_fstat(descriptor: int) -> os.stat_result:
        nonlocal injected
        target_index = 0 if phase == "after-open-a" else 1
        if (
            phase in {"after-open-a", "after-open-b"}
            and len(output_descriptors) > target_index
            and descriptor == output_descriptors[target_index]
            and not injected
        ):
            injected = True
            raise original
        return real_fstat(descriptor)

    def interrupting_write(descriptor: int, payload: object) -> int:
        nonlocal injected
        if (
            phase == "during-write"
            and output_descriptors
            and descriptor == output_descriptors[0]
            and not injected
        ):
            injected = True
            view = memoryview(payload)  # type: ignore[arg-type]
            real_write(descriptor, view[:17])
            raise original
        return real_write(descriptor, payload)  # type: ignore[arg-type]

    def interrupting_fsync(descriptor: int) -> None:
        nonlocal injected
        if (
            phase == "after-write"
            and output_descriptors
            and descriptor == output_descriptors[0]
            and not injected
        ):
            injected = True
            raise original
        if (
            phase == "between-final-checks"
            and work_descriptor is not None
            and descriptor == work_descriptor
            and len(output_descriptors) == 2
            and not injected
        ):
            injected = True
            raise original
        real_fsync(descriptor)

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    monkeypatch.setattr(module_globals["os"], "fstat", interrupting_fstat)
    monkeypatch.setattr(module_globals["os"], "write", interrupting_write)
    monkeypatch.setattr(module_globals["os"], "fsync", interrupting_fsync)

    previous_umask = os.umask(0o077)
    try:
        with pytest.raises(type(original)) as caught:
            module_globals["_run"](source, build_python, work_root)
        assert caught.value is original
        assert _current_umask() == 0o077
    finally:
        os.umask(previous_umask)

    assert injected
    assert output_descriptors
    for descriptor in output_descriptors:
        with pytest.raises(OSError):
            real_fstat(descriptor)
    expected_directories = (
        ("wheel-a",)
        if phase in {"after-open-a", "during-write", "after-write"}
        else ("wheel-a", "wheel-b")
    )
    for directory in expected_directories:
        assert (work_root / directory / PRODUCER_FILENAME).read_bytes() == b""
    if "wheel-b" not in expected_directories:
        assert list((work_root / "wheel-b").iterdir()) == []


@pytest.mark.parametrize("exception_kind", ("keyboard-interrupt", "system-exit"))
def test_build_post_commit_close_interrupt_preserves_success_and_external_fd(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception_kind: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    original = _injected_base_exception(exception_kind, "partial commit close")
    real_open = os.open
    real_fstat = os.fstat
    real_close = os.close
    real_dup = os.dup
    output_descriptors: list[int] = []
    duplicate_descriptors: list[int] = []
    external_path = tmp_path / "external"
    external_path.write_bytes(b"must survive")
    external_descriptor: int | None = None
    first_closed = False
    injected = False

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == PRODUCER_FILENAME:
            output_descriptors.append(descriptor)
        return descriptor

    def recording_dup(descriptor: int) -> int:
        duplicate = real_dup(descriptor)
        if descriptor in output_descriptors:
            duplicate_descriptors.append(duplicate)
        return duplicate

    def interrupting_close(descriptor: int) -> None:
        nonlocal external_descriptor, first_closed, injected
        if len(output_descriptors) == 2 and descriptor == output_descriptors[0]:
            real_close(descriptor)
            external_descriptor = real_open(external_path, os.O_RDWR)
            assert external_descriptor == descriptor
            first_closed = True
            return
        if (
            len(output_descriptors) == 2
            and descriptor == output_descriptors[1]
            and first_closed
            and not injected
        ):
            injected = True
            raise original
        real_close(descriptor)

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    monkeypatch.setattr(module_globals["os"], "dup", recording_dup)
    monkeypatch.setattr(module_globals["os"], "close", interrupting_close)

    try:
        result = module_globals["_run"](source, build_python, work_root)
        assert result[2] == len(b"identical test wheel")
        assert injected
        assert external_descriptor is not None
        assert real_fstat(external_descriptor).st_size == len(b"must survive")
        assert external_path.read_bytes() == b"must survive"
        assert all(
            (work_root / directory / PRODUCER_FILENAME).read_bytes()
            == b"identical test wheel"
            for directory in ("wheel-a", "wheel-b")
        )
        for descriptor in duplicate_descriptors:
            with pytest.raises(OSError):
                real_fstat(descriptor)
    finally:
        if external_descriptor is not None:
            real_close(external_descriptor)
        if len(output_descriptors) == 2:
            try:
                real_close(output_descriptors[1])
            except OSError:
                pass


@pytest.mark.parametrize("exception_kind", ("keyboard-interrupt", "system-exit"))
@pytest.mark.parametrize("phase", ("work-root", "wheel-a", "wheel-b"))
def test_build_initial_pin_base_exception_closes_all_fds_and_restores_umask(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    exception_kind: str,
    phase: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    before = _caller_surface(work_root)
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    original = _injected_base_exception(exception_kind, phase)
    real_open = os.open
    real_fstat = os.fstat
    retained: dict[str, int] = {}
    injected = False

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == work_root:
            retained["work-root"] = descriptor
        elif path in {"wheel-a", "wheel-b"}:
            retained[str(path)] = descriptor
        return descriptor

    def interrupting_fstat(descriptor: int) -> os.stat_result:
        nonlocal injected
        if descriptor == retained.get(phase) and not injected:
            injected = True
            raise original
        return real_fstat(descriptor)

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    monkeypatch.setattr(module_globals["os"], "fstat", interrupting_fstat)

    previous_umask = os.umask(0o077)
    try:
        with pytest.raises(type(original)) as caught:
            module_globals["_run"](source, build_python, work_root)
        assert caught.value is original
        assert _current_umask() == 0o077
    finally:
        os.umask(previous_umask)

    assert injected
    assert retained
    for descriptor in retained.values():
        with pytest.raises(OSError):
            real_fstat(descriptor)
    assert _caller_surface(work_root) == before


@pytest.mark.parametrize("cleanup_operation", ("fstat", "ftruncate", "fsync", "close"))
@pytest.mark.parametrize("secondary_kind", ("keyboard-interrupt", "system-exit"))
def test_build_cleanup_base_exception_never_masks_original_or_aborts_resources(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_operation: str,
    secondary_kind: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    original = KeyboardInterrupt("original publication interrupt")
    secondary = _injected_base_exception(secondary_kind, cleanup_operation)
    real_open = os.open
    real_fstat = os.fstat
    real_ftruncate = os.ftruncate
    real_fsync = os.fsync
    real_close = os.close
    real_dup = os.dup
    work_descriptor: int | None = None
    output_descriptors: list[int] = []
    cleanup_descriptors: list[int] = []
    cleanup_active = False
    original_injected = False
    secondary_injected = False
    bad_close_attempts: list[int] = []

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal work_descriptor
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == work_root:
            work_descriptor = descriptor
        elif path == PRODUCER_FILENAME:
            output_descriptors.append(descriptor)
        return descriptor

    def recording_dup(descriptor: int) -> int:
        duplicate = real_dup(descriptor)
        if descriptor in output_descriptors:
            cleanup_descriptors.append(duplicate)
        return duplicate

    def interrupting_fstat(descriptor: int) -> os.stat_result:
        nonlocal secondary_injected
        if (
            cleanup_active
            and cleanup_operation == "fstat"
            and cleanup_descriptors
            and descriptor == cleanup_descriptors[0]
            and not secondary_injected
        ):
            secondary_injected = True
            raise secondary
        return real_fstat(descriptor)

    def interrupting_ftruncate(descriptor: int, length: int) -> None:
        nonlocal secondary_injected
        real_ftruncate(descriptor, length)
        if (
            cleanup_active
            and cleanup_operation == "ftruncate"
            and cleanup_descriptors
            and descriptor == cleanup_descriptors[0]
            and not secondary_injected
        ):
            secondary_injected = True
            raise secondary

    def interrupting_fsync(descriptor: int) -> None:
        nonlocal cleanup_active, original_injected, secondary_injected
        if (
            not cleanup_active
            and work_descriptor is not None
            and descriptor == work_descriptor
            and len(output_descriptors) == 2
        ):
            cleanup_active = True
            original_injected = True
            raise original
        real_fsync(descriptor)
        if (
            cleanup_active
            and cleanup_operation == "fsync"
            and cleanup_descriptors
            and descriptor == cleanup_descriptors[0]
            and not secondary_injected
        ):
            secondary_injected = True
            raise secondary

    def interrupting_close(descriptor: int) -> None:
        nonlocal secondary_injected
        try:
            real_close(descriptor)
        except OSError:
            bad_close_attempts.append(descriptor)
            raise
        if (
            cleanup_active
            and cleanup_operation == "close"
            and cleanup_descriptors
            and descriptor == cleanup_descriptors[0]
            and not secondary_injected
        ):
            secondary_injected = True
            raise secondary

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    monkeypatch.setattr(module_globals["os"], "dup", recording_dup)
    monkeypatch.setattr(module_globals["os"], "fstat", interrupting_fstat)
    monkeypatch.setattr(module_globals["os"], "ftruncate", interrupting_ftruncate)
    monkeypatch.setattr(module_globals["os"], "fsync", interrupting_fsync)
    monkeypatch.setattr(module_globals["os"], "close", interrupting_close)

    previous_umask = os.umask(0o077)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            module_globals["_run"](source, build_python, work_root)
        assert caught.value is original
        assert _current_umask() == 0o077
    finally:
        os.umask(previous_umask)

    assert original_injected
    assert secondary_injected
    assert bad_close_attempts == []
    assert len(output_descriptors) == 2
    assert len(cleanup_descriptors) == 2
    for directory in ("wheel-a", "wheel-b"):
        assert (work_root / directory / PRODUCER_FILENAME).read_bytes() == b""
    for descriptor in (*output_descriptors, *cleanup_descriptors):
        with pytest.raises(OSError):
            real_fstat(descriptor)


@pytest.mark.parametrize("secondary_kind", ("keyboard-interrupt", "system-exit"))
def test_build_cleanup_persistent_ftruncate_base_exception_leaves_pair_unsealable(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    producer_wheel_pair: tuple[Path, Path],
    secondary_kind: str,
) -> None:
    source = _copy_seal_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    wheel = producer_wheel_pair[0].read_bytes()
    module_globals["_build_once"] = lambda *_arguments: wheel
    original = KeyboardInterrupt("original publication interrupt")
    secondary = _injected_base_exception(secondary_kind, "persistent ftruncate")
    real_open = os.open
    real_fstat = os.fstat
    real_ftruncate = os.ftruncate
    real_fsync = os.fsync
    real_dup = os.dup
    work_descriptor: int | None = None
    output_descriptors: list[int] = []
    cleanup_descriptors: list[int] = []
    cleanup_active = False
    truncate_attempts: list[int] = []

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal work_descriptor
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == work_root:
            work_descriptor = descriptor
        elif path == PRODUCER_FILENAME:
            output_descriptors.append(descriptor)
        return descriptor

    def recording_dup(descriptor: int) -> int:
        duplicate = real_dup(descriptor)
        if descriptor in output_descriptors:
            cleanup_descriptors.append(duplicate)
        return duplicate

    def interrupting_fsync(descriptor: int) -> None:
        nonlocal cleanup_active
        if (
            not cleanup_active
            and work_descriptor is not None
            and descriptor == work_descriptor
            and len(output_descriptors) == 2
        ):
            cleanup_active = True
            raise original
        real_fsync(descriptor)

    def persistently_failing_ftruncate(descriptor: int, _length: int) -> None:
        if cleanup_active and descriptor in cleanup_descriptors:
            truncate_attempts.append(descriptor)
            raise secondary
        real_ftruncate(descriptor, _length)

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    monkeypatch.setattr(module_globals["os"], "dup", recording_dup)
    monkeypatch.setattr(module_globals["os"], "fsync", interrupting_fsync)
    monkeypatch.setattr(
        module_globals["os"],
        "ftruncate",
        persistently_failing_ftruncate,
    )

    previous_umask = os.umask(0o077)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            module_globals["_run"](source, build_python, work_root)
        assert caught.value is original
        assert _current_umask() == 0o077
    finally:
        os.umask(previous_umask)

    assert set(truncate_attempts) == set(cleanup_descriptors)
    assert len(output_descriptors) == 2
    assert len(cleanup_descriptors) == 2
    for descriptor in (*output_descriptors, *cleanup_descriptors):
        with pytest.raises(OSError):
            real_fstat(descriptor)
    assert all(
        (work_root / directory / PRODUCER_FILENAME).read_bytes() != wheel
        for directory in ("wheel-a", "wheel-b")
    )

    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    producer_lock = tmp_path / "producer.lock"
    completed = _run_tool(
        "seal_producer_build.py",
        "--source-root",
        str(source),
        "--wheel-dir-a",
        str(work_root / "wheel-a"),
        "--wheel-dir-b",
        str(work_root / "wheel-b"),
        "--wheelhouse",
        str(wheelhouse),
        "--producer-lock",
        str(producer_lock),
    )
    assert completed.returncode == 2
    assert not producer_lock.exists()
    assert not (wheelhouse / PRODUCER_FILENAME).exists()


@pytest.mark.parametrize("probe_kind", ("keyboard-interrupt", "system-exit"))
@pytest.mark.parametrize("block_primary_probe", (False, True))
@pytest.mark.parametrize("block_after_fallback_auth", (False, True))
def test_build_cleanup_uses_fresh_dup_when_retained_cleanup_probe_is_unavailable(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_kind: str,
    block_primary_probe: bool,
    block_after_fallback_auth: bool,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    original = KeyboardInterrupt("original publication interrupt")
    probe_error = _injected_base_exception(probe_kind, "cleanup probe")
    real_open = os.open
    real_dup = os.dup
    real_fstat = os.fstat
    real_stat = os.stat
    real_fsync = os.fsync
    work_descriptor: int | None = None
    primary_descriptors: list[int] = []
    retained_cleanup_descriptors: list[int] = []
    fallback_descriptors: list[int] = []
    fallback_authentications: dict[int, int] = {}
    cleanup_active = False

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal work_descriptor
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == work_root:
            work_descriptor = descriptor
        elif path == PRODUCER_FILENAME:
            primary_descriptors.append(descriptor)
        return descriptor

    def recording_dup(descriptor: int) -> int:
        duplicate = real_dup(descriptor)
        if descriptor in primary_descriptors:
            if cleanup_active:
                fallback_descriptors.append(duplicate)
            else:
                retained_cleanup_descriptors.append(duplicate)
        return duplicate

    def failing_fstat(descriptor: int) -> os.stat_result:
        if cleanup_active and descriptor in fallback_descriptors:
            if (
                block_after_fallback_auth
                and fallback_authentications.get(descriptor, 0) >= 1
            ):
                raise probe_error
            result = real_fstat(descriptor)
            fallback_authentications[descriptor] = (
                fallback_authentications.get(descriptor, 0) + 1
            )
            return result
        if cleanup_active and (
            descriptor in retained_cleanup_descriptors
            or block_primary_probe and descriptor in primary_descriptors
        ):
            raise probe_error
        return real_fstat(descriptor)

    def failing_stat(
        path: os.PathLike[str] | str | bytes | int,
        *arguments: object,
        **keywords: object,
    ) -> os.stat_result:
        if (
            cleanup_active
            and block_after_fallback_auth
            and path in fallback_descriptors
            and fallback_authentications.get(path, 0) >= 1
        ):
            raise probe_error
        if cleanup_active and (
            path in retained_cleanup_descriptors
            or block_primary_probe and path in primary_descriptors
        ):
            raise probe_error
        return real_stat(path, *arguments, **keywords)

    def interrupting_fsync(descriptor: int) -> None:
        nonlocal cleanup_active
        if (
            not cleanup_active
            and work_descriptor is not None
            and descriptor == work_descriptor
            and len(primary_descriptors) == 2
        ):
            cleanup_active = True
            raise original
        real_fsync(descriptor)

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    monkeypatch.setattr(module_globals["os"], "dup", recording_dup)
    monkeypatch.setattr(module_globals["os"], "fstat", failing_fstat)
    monkeypatch.setattr(module_globals["os"], "stat", failing_stat)
    monkeypatch.setattr(module_globals["os"], "fsync", interrupting_fsync)

    with pytest.raises(KeyboardInterrupt) as caught:
        module_globals["_run"](source, build_python, work_root)
    assert caught.value is original
    assert len(primary_descriptors) == 2
    assert len(retained_cleanup_descriptors) == 2
    assert len(fallback_descriptors) == 2
    assert set(fallback_authentications) == set(fallback_descriptors)
    assert all(
        (work_root / directory / PRODUCER_FILENAME).read_bytes() == b""
        for directory in ("wheel-a", "wheel-b")
    )
    for descriptor in (
        *primary_descriptors,
        *retained_cleanup_descriptors,
        *fallback_descriptors,
    ):
        with pytest.raises(OSError):
            real_fstat(descriptor)


def test_close_best_effort_never_retries_reused_same_inode_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_close_best_effort"].__globals__
    target = tmp_path / "same-inode"
    target.write_bytes(b"external data")
    descriptor = os.open(target, os.O_RDONLY)
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    replacement_descriptor: int | None = None
    close_attempts = 0

    def closing_reopening_then_interrupting(value: int) -> None:
        nonlocal replacement_descriptor, close_attempts
        close_attempts += 1
        real_close(value)
        replacement_descriptor = real_open(target, os.O_RDONLY)
        assert replacement_descriptor == value
        raise KeyboardInterrupt("close completed before interrupt")

    monkeypatch.setattr(
        module_globals["os"],
        "close",
        closing_reopening_then_interrupting,
    )

    try:
        module_globals["_close_best_effort"]((descriptor,))
        assert close_attempts == 1
        assert replacement_descriptor == descriptor
        assert real_fstat(replacement_descriptor).st_size == len(b"external data")
    finally:
        if replacement_descriptor is not None:
            real_close(replacement_descriptor)


def test_cleanup_never_recloses_reused_same_inode_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_cleanup_owned_outputs"].__globals__
    target = tmp_path / "owned-wheel"
    target.write_bytes(b"complete wheel bytes")
    primary_descriptor = os.open(target, os.O_RDWR)
    cleanup_descriptor = os.dup(primary_descriptor)
    opened = os.fstat(primary_descriptor)
    output = module_globals["_OwnedOutput"](
        primary_descriptor=primary_descriptor,
        cleanup_descriptor=cleanup_descriptor,
        owner_identity=(opened.st_dev, opened.st_ino),
    )
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    replacement_descriptor: int | None = None
    cleanup_close_attempts = 0

    def closing_reopening_then_interrupting(descriptor: int) -> None:
        nonlocal replacement_descriptor, cleanup_close_attempts
        if descriptor == cleanup_descriptor:
            cleanup_close_attempts += 1
            real_close(descriptor)
            replacement_descriptor = real_open(target, os.O_RDONLY)
            if replacement_descriptor != descriptor:
                os.dup2(replacement_descriptor, descriptor)
                real_close(replacement_descriptor)
                replacement_descriptor = descriptor
            raise SystemExit("cleanup close completed before interrupt")
        real_close(descriptor)

    monkeypatch.setattr(
        module_globals["os"],
        "close",
        closing_reopening_then_interrupting,
    )

    try:
        module_globals["_cleanup_owned_outputs"]({"wheel-a": output})
        assert cleanup_close_attempts == 1
        assert replacement_descriptor == cleanup_descriptor
        real_fstat(replacement_descriptor)
        assert target.read_bytes() == b""
    finally:
        if replacement_descriptor is not None:
            real_close(replacement_descriptor)


def test_build_closes_every_initial_pin_once_when_close_closes_then_raises(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    before = _caller_surface(work_root)
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    real_open = os.open
    real_fstat = os.fstat
    real_close = os.close
    retained: list[int] = []
    close_attempts: list[int] = []
    wheel_b_descriptor: int | None = None

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal wheel_b_descriptor
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == work_root or path in {"wheel-a", "wheel-b"}:
            retained.append(descriptor)
        if path == "wheel-b":
            wheel_b_descriptor = descriptor
        return descriptor

    def failing_fstat(descriptor: int) -> os.stat_result:
        if descriptor == wheel_b_descriptor:
            raise OSError("injected initial pin authentication failure")
        return real_fstat(descriptor)

    def closing_then_raising(descriptor: int) -> None:
        close_attempts.append(descriptor)
        real_close(descriptor)
        raise OSError("injected close-after-success failure")

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    monkeypatch.setattr(module_globals["os"], "fstat", failing_fstat)
    monkeypatch.setattr(module_globals["os"], "close", closing_then_raising)

    with pytest.raises((error_type, OSError), match="pin|authentication"):
        module_globals["_run"](source, build_python, work_root)

    assert len(retained) == 3
    assert sorted(close_attempts) == sorted(retained)
    assert len(close_attempts) == len(set(close_attempts))
    assert _caller_surface(work_root) == before


@pytest.mark.parametrize("outcome", ("success", "cleanup"))
def test_build_close_after_success_never_masks_publication_or_aborts_cleanup(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    real_publish = module_globals["_publish_pair"]
    real_open = os.open
    real_close = os.close
    active = False
    output_opens = 0
    retained: set[int] = set()
    close_attempts: list[int] = []
    bad_descriptor_closes: list[int] = []

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal output_opens
        if path == PRODUCER_FILENAME:
            output_opens += 1
            if outcome == "cleanup" and output_opens == 2:
                raise PermissionError("injected second output open failure")
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == work_root or path in {"wheel-a", "wheel-b", PRODUCER_FILENAME}:
            retained.add(descriptor)
        return descriptor

    def activating_publish(*arguments: object, **keywords: object) -> None:
        nonlocal active
        active = True
        return real_publish(*arguments, **keywords)

    def closing_then_raising(descriptor: int) -> None:
        if active:
            close_attempts.append(descriptor)
            try:
                real_close(descriptor)
            except OSError:
                bad_descriptor_closes.append(descriptor)
                raise
            raise OSError("injected close-after-success failure")
        real_close(descriptor)

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    monkeypatch.setattr(module_globals["os"], "close", closing_then_raising)
    module_globals["_publish_pair"] = activating_publish

    if outcome == "success":
        result = module_globals["_run"](source, build_python, work_root)
        assert result[2] == len(b"identical test wheel")
        assert output_opens == 2
        assert all(
            (work_root / directory / PRODUCER_FILENAME).is_file()
            for directory in ("wheel-a", "wheel-b")
        )
    else:
        with pytest.raises(
            (PermissionError, module_globals["ProducerBuildError"]),
            match="second output|cannot be created",
        ):
            module_globals["_run"](source, build_python, work_root)
        assert output_opens == 2
        assert (work_root / "wheel-a" / PRODUCER_FILENAME).read_bytes() == b""
        assert list((work_root / "wheel-b").iterdir()) == []
    assert retained <= set(close_attempts)
    assert bad_descriptor_closes == []
    for descriptor in retained:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_build_uses_safe_interpreter_cwd_and_allowlisted_environment(
    tmp_path: Path,
    build_python: Path,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    caller_cwd = tmp_path / "caller-cwd"
    caller_pythonpath = tmp_path / "caller-pythonpath"
    caller_cwd.mkdir()
    caller_pythonpath.mkdir()
    cwd_marker = tmp_path / "cwd-build-imported"
    pythonpath_marker = tmp_path / "pythonpath-build-imported"
    for directory, marker in (
        (caller_cwd, cwd_marker),
        (caller_pythonpath, pythonpath_marker),
    ):
        (directory / "build.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
            "raise SystemExit(97)\n",
            encoding="utf-8",
        )
    invocation_log = tmp_path / "build-python-invocations.jsonl"
    wrapper = build_python.parent / "recording-python"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n"
        f"with Path({str(invocation_log)!r}).open('a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), "
        "'environment': dict(os.environ)}, sort_keys=True) + '\\n')\n"
        f"os.execv({str(build_python)!r}, [{str(build_python)!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    caller_environment = {
        **os.environ,
        "PYTHONPATH": str(caller_pythonpath),
        "SFEP_AMBIENT_BUILD_VARIABLE": "must-not-leak",
        "SETUPTOOLS_SCM_PRETEND_VERSION": "9.9.9",
    }
    work_root = prepare_producer_work_root(tmp_path / "isolated-work")

    result = run_producer_build(
        source,
        wrapper,
        work_root,
        cwd=caller_cwd,
        env=caller_environment,
    )

    assert result.returncode == 0, result.stderr
    assert not cwd_marker.exists()
    assert not pythonpath_marker.exists()
    invocations = [
        json.loads(line)
        for line in invocation_log.read_text(encoding="utf-8").splitlines()
    ]
    assert len(invocations) == 2
    allowed_environment = {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PIP_DISABLE_PIP_VERSION_CHECK",
        "PIP_NO_INDEX",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "PYTHONNOUSERSITE",
        "SOURCE_DATE_EPOCH",
        "TMPDIR",
        "TZ",
    }
    for invocation in invocations:
        assert invocation["argv"][:4] == ["-P", "-s", "-m", "build"]
        assert Path(invocation["cwd"]).name in {"stage-a", "stage-b"}
        assert set(invocation["environment"]) <= allowed_environment
        assert invocation["environment"]["PYTHONHASHSEED"] == "0"
        assert invocation["environment"]["SOURCE_DATE_EPOCH"] == "1735689600"


def test_source_mutation_changes_provenance_not_the_source_tree(
    tmp_path: Path,
    build_python: Path,
) -> None:
    source = _copy_source_root(tmp_path / "mutated-analysis")
    target = source / "equipment_quality/__init__.py"
    target.write_bytes(target.read_bytes() + b"# test-side source mutation\n")
    before = source_tree_bytes(source)
    expected = independently_expected_provenance(source)

    work_root = prepare_producer_work_root(tmp_path / "mutated-work")
    result = run_producer_build(source, build_python, work_root)

    assert result.returncode == 0, result.stderr
    wheel = work_root / "wheel-a" / PRODUCER_FILENAME
    assert wheel_resource(wheel, PROVENANCE_PATH) == expected
    assert json.loads(expected)["sourceSha256"] != EXPECTED_SOURCE_DIGEST
    assert source_tree_bytes(source) == before
    assert not (source / PROVENANCE_PATH).exists()


@pytest.mark.parametrize("tool", ("build_producer.py", "seal_producer_build.py"))
@pytest.mark.parametrize(
    "case",
    ("post-scandir-addition", "file-replacement", "directory-replacement"),
)
def test_source_snapshot_rejects_persistent_inventory_and_identity_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    case: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    package = source / "equipment_quality"
    namespace = runpy.run_path(str(TOOLS_ROOT / tool))
    module_globals = namespace["_source_snapshot"].__globals__
    error_type = module_globals[
        "ProducerBuildError" if tool == "build_producer.py" else "ProducerSealError"
    ]
    injected = False
    if case == "post-scandir-addition":
        real_scandir = os.scandir
        package_identity = (os.lstat(package).st_dev, os.lstat(package).st_ino)

        def adding_scandir(path: os.PathLike[str] | str | int):
            nonlocal injected
            scanner = real_scandir(path)
            result = os.fstat(path) if isinstance(path, int) else os.lstat(path)
            if not injected and (result.st_dev, result.st_ino) == package_identity:
                entries = list(scanner)
                scanner.close()
                (package / "late.py").write_bytes(b"LATE = True\n")
                injected = True
                return iter(entries)
            return scanner

        monkeypatch.setattr(module_globals["os"], "scandir", adding_scandir)
    else:
        real_read = module_globals["_read_regular_once"]
        target = package / "__init__.py"

        def replacing_read(path: Path, label: str, *arguments: object) -> bytes:
            nonlocal injected
            if not injected and Path(path) == target:
                if case == "file-replacement":
                    replacement = package / ".replacement"
                    shutil.copy2(target, replacement)
                    os.replace(replacement, target)
                else:
                    original = source / "equipment_quality-original"
                    package.rename(original)
                    shutil.copytree(original, package)
                injected = True
            return real_read(path, label, *arguments)

        module_globals["_read_regular_once"] = replacing_read

    with pytest.raises(error_type, match="changed|inventory|identity"):
        namespace["_source_snapshot"](source)

    assert injected


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("relative-source", "absolute"),
        ("symlink-source", "source root"),
        ("symlink-python", "build Python"),
        ("nonempty-work", "empty"),
        ("source-overlap", "overlap"),
        ("case-aliased-source-overlap", "overlap"),
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
    work_root = prepare_producer_work_root(tmp_path / "work")
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
        (work_root / "wheel-a/occupied").write_bytes(b"occupied")
    elif case == "source-overlap":
        work_root = prepare_producer_work_root(source / "work")
    elif case == "case-aliased-source-overlap":
        aliased_source = source.with_name(source.name.swapcase())
        if not aliased_source.exists() or not os.path.samefile(
            aliased_source,
            source,
        ):
            pytest.skip("requires a case-insensitive filesystem")
        work_root = prepare_producer_work_root(aliased_source / "work")
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


@pytest.mark.parametrize(
    "case",
    ("symlink-to-source", "directory-replacement", "persistent-addition"),
)
def test_build_rechecks_the_pinned_work_root_before_any_output_write(
    tmp_path: Path,
    build_python: Path,
    case: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    real_snapshot = module_globals["_source_snapshot"]
    mutated = False

    def mutating_snapshot(path: Path) -> dict[str, bytes]:
        nonlocal mutated
        snapshot = real_snapshot(path)
        if case == "persistent-addition":
            (work_root / "late").write_bytes(b"late")
        else:
            if work_root.exists() or work_root.is_symlink():
                work_root.rename(tmp_path / "original-work")
            if case == "symlink-to-source":
                work_root.symlink_to(source, target_is_directory=True)
            else:
                prepare_producer_work_root(work_root)
        mutated = True
        return snapshot

    module_globals["_source_snapshot"] = mutating_snapshot

    with pytest.raises(error_type, match="changed|empty|identity|safe|symlink"):
        module_globals["_run"](source, build_python, work_root)

    assert mutated
    assert not (source / "wheel-a" / PRODUCER_FILENAME).exists()
    assert not (source / "wheel-b" / PRODUCER_FILENAME).exists()
    assert not list(tmp_path.rglob(".sfep-build-*"))


def test_build_rejects_an_ancestor_symlink_retargeted_into_the_source(
    tmp_path: Path,
    build_python: Path,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    safe = tmp_path / "safe"
    safe.mkdir()
    link = tmp_path / "work-link"
    link.symlink_to(safe, target_is_directory=True)
    work_root = link / "work"
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    real_paths_overlap = module_globals["_paths_overlap"]
    retargeted = False

    def retargeting_paths_overlap(first: Path, second: Path) -> bool:
        nonlocal retargeted
        overlap = real_paths_overlap(first, second)
        if not retargeted and not overlap:
            link.unlink()
            link.symlink_to(source / "equipment_quality", target_is_directory=True)
            retargeted = True
        return overlap

    module_globals["_paths_overlap"] = retargeting_paths_overlap

    with pytest.raises(error_type, match="overlap|safe|symlink"):
        module_globals["_run"](source, build_python, work_root)

    assert retargeted
    assert not (source / "equipment_quality/work").exists()
    assert not list(tmp_path.rglob(".sfep-build-*"))


def test_build_rechecks_the_authenticated_source_before_publishing(
    tmp_path: Path,
    build_python: Path,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    target = source / "equipment_quality/__init__.py"
    builds = 0

    def mutating_build(*_arguments: object) -> bytes:
        nonlocal builds
        builds += 1
        if builds == 2:
            target.write_bytes(target.read_bytes() + b"# late mutation\n")
        return b"identical test wheel"

    module_globals["_build_once"] = mutating_build

    with pytest.raises(error_type, match="source|changed|inventory|identity"):
        module_globals["_run"](source, build_python, work_root)

    assert builds == 2
    assert list((work_root / "wheel-a").iterdir()) == []
    assert list((work_root / "wheel-b").iterdir()) == []


@pytest.mark.parametrize("mutated_directory", ("wheel-a", "wheel-b"))
def test_build_reauthenticates_both_outputs_after_the_last_source_check(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutated_directory: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    payload = b"identical test wheel"
    module_globals["_build_once"] = lambda *_arguments: payload
    real_open = os.open
    real_fsync = os.fsync
    real_recheck_source = module_globals["_recheck_source_attestation"]
    work_descriptor: int | None = None
    final_work_sync = False
    mutated = False

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal work_descriptor
        descriptor = real_open(path, flags, *arguments, **keywords)
        if path == work_root:
            work_descriptor = descriptor
        return descriptor

    def recording_fsync(descriptor: int) -> None:
        nonlocal final_work_sync
        real_fsync(descriptor)
        if (
            work_descriptor is not None
            and descriptor == work_descriptor
            and all(
                (work_root / name / PRODUCER_FILENAME).exists()
                for name in ("wheel-a", "wheel-b")
            )
        ):
            final_work_sync = True

    def mutating_final_source_check(
        path: Path,
        attestation: object,
    ) -> None:
        nonlocal mutated
        real_recheck_source(path, attestation)
        if final_work_sync and not mutated:
            target = work_root / mutated_directory / PRODUCER_FILENAME
            target.write_bytes(b"X" + payload[1:])
            mutated = True

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    monkeypatch.setattr(module_globals["os"], "fsync", recording_fsync)
    module_globals["_recheck_source_attestation"] = mutating_final_source_check

    with pytest.raises(error_type, match="changed|authentication|publication"):
        module_globals["_run"](source, build_python, work_root)

    assert mutated
    for directory in ("wheel-a", "wheel-b"):
        assert (work_root / directory / PRODUCER_FILENAME).read_bytes() == b""


def test_build_final_commit_orders_source_then_both_retained_output_authentications(
    tmp_path: Path,
    build_python: Path,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    payload = b"identical test wheel"
    module_globals["_build_once"] = lambda *_arguments: payload
    real_recheck_source = module_globals["_recheck_source_attestation"]
    real_authenticate = module_globals["_authenticate_open_file"]
    events: list[tuple[str, str | None]] = []

    def recording_source_check(path: Path, attestation: object) -> None:
        real_recheck_source(path, attestation)
        events.append(("source", None))

    def recording_output_authentication(
        descriptor: int,
        expected: bytes,
        label: str,
    ) -> object:
        authenticated = real_authenticate(descriptor, expected, label)
        events.append(("output", label))
        return authenticated

    module_globals["_recheck_source_attestation"] = recording_source_check
    module_globals["_authenticate_open_file"] = recording_output_authentication

    module_globals["_run"](source, build_python, work_root)

    assert events[-3:] == [
        ("source", None),
        ("output", "wheel-a producer wheel"),
        ("output", "wheel-b producer wheel"),
    ]


@pytest.mark.parametrize("mutated_directory", ("wheel-a", "wheel-b"))
def test_build_rejects_output_mutation_during_the_final_prepared_inventory(
    tmp_path: Path,
    build_python: Path,
    mutated_directory: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    payload = b"identical test wheel"
    module_globals["_build_once"] = lambda *_arguments: payload
    real_reauthenticate = module_globals[
        "_reauthenticate_owned_outputs_for_commit"
    ]
    real_inventory = module_globals["_directory_inventory_fd"]
    final_bytes_verified = False
    mutated = False

    def recording_reauthentication(
        owned: object,
        expected: bytes,
    ) -> None:
        nonlocal final_bytes_verified
        real_reauthenticate(owned, expected)
        final_bytes_verified = True

    def mutating_inventory(
        descriptor: int,
        label: str,
    ) -> object:
        nonlocal mutated
        if (
            final_bytes_verified
            and label == f"prepared {mutated_directory} output directory"
            and not mutated
        ):
            target = work_root / mutated_directory / PRODUCER_FILENAME
            target.write_bytes(b"X" + payload[1:])
            mutated = True
        return real_inventory(descriptor, label)

    module_globals[
        "_reauthenticate_owned_outputs_for_commit"
    ] = recording_reauthentication
    module_globals["_directory_inventory_fd"] = mutating_inventory

    with pytest.raises(error_type, match="inventory|changed|publication"):
        module_globals["_run"](source, build_python, work_root)

    assert mutated
    for directory in ("wheel-a", "wheel-b"):
        assert (work_root / directory / PRODUCER_FILENAME).read_bytes() == b""


@pytest.mark.parametrize(
    "drift",
    ("source-during-output-auth", "wheel-a-during-wheel-b-inventory"),
)
def test_build_linearization_drift_is_rejected_by_the_later_seal(
    tmp_path: Path,
    build_python: Path,
    producer_wheel_pair: tuple[Path, Path],
    drift: str,
) -> None:
    source = _copy_seal_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    wheel = producer_wheel_pair[0].read_bytes()
    module_globals["_build_once"] = lambda *_arguments: wheel
    real_authenticate = module_globals["_authenticate_open_file"]
    real_reauthenticate = module_globals[
        "_reauthenticate_owned_outputs_for_commit"
    ]
    real_inventory = module_globals["_directory_inventory_fd"]
    target = source / "equipment_quality/__init__.py"
    wheel_a_authentications = 0
    final_bytes_verified = False
    mutated = False

    def mutating_final_output_authentication(
        descriptor: int,
        expected: bytes,
        label: str,
    ) -> object:
        nonlocal wheel_a_authentications, mutated
        authenticated = real_authenticate(descriptor, expected, label)
        if (
            drift == "source-during-output-auth"
            and label == "wheel-a producer wheel"
        ):
            wheel_a_authentications += 1
            if wheel_a_authentications == 2:
                target.write_bytes(target.read_bytes() + b"# post-linearization drift\n")
                mutated = True
        return authenticated

    def recording_reauthentication(
        owned: object,
        expected: bytes,
    ) -> None:
        nonlocal final_bytes_verified
        real_reauthenticate(owned, expected)
        final_bytes_verified = True

    def mutating_final_inventory(
        descriptor: int,
        label: str,
    ) -> object:
        nonlocal mutated
        if (
            drift == "wheel-a-during-wheel-b-inventory"
            and final_bytes_verified
            and label == "prepared wheel-b output directory"
            and not mutated
        ):
            output = work_root / "wheel-a" / PRODUCER_FILENAME
            output.write_bytes(b"X" + wheel[1:])
            mutated = True
        return real_inventory(descriptor, label)

    module_globals["_authenticate_open_file"] = mutating_final_output_authentication
    module_globals[
        "_reauthenticate_owned_outputs_for_commit"
    ] = recording_reauthentication
    module_globals["_directory_inventory_fd"] = mutating_final_inventory

    # Exact earlier and later observations overlap before either injected drift,
    # so build may linearize there; the seal must reject the now-current state.
    module_globals["_run"](source, build_python, work_root)

    assert mutated
    wheel_a = work_root / "wheel-a" / PRODUCER_FILENAME
    wheel_b = work_root / "wheel-b" / PRODUCER_FILENAME
    if drift == "source-during-output-auth":
        assert wheel_a.read_bytes() == wheel_b.read_bytes() == wheel
    else:
        assert wheel_a.read_bytes() != wheel
        assert wheel_b.read_bytes() == wheel
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    producer_lock = tmp_path / "producer.lock"
    sealed = _run_tool(
        "seal_producer_build.py",
        "--source-root",
        str(source),
        "--wheel-dir-a",
        str(work_root / "wheel-a"),
        "--wheel-dir-b",
        str(work_root / "wheel-b"),
        "--wheelhouse",
        str(wheelhouse),
        "--producer-lock",
        str(producer_lock),
    )
    assert sealed.returncode == 2
    assert not producer_lock.exists()
    assert not (wheelhouse / PRODUCER_FILENAME).exists()


def test_build_rejects_replaced_output_instead_of_adopting_its_fingerprint(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = prepare_producer_work_root(tmp_path / "work")
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    real_utime = os.utime
    injected = False

    def replacing_utime(*arguments: object, **keywords: object) -> None:
        nonlocal injected
        target = work_root / "wheel-a" / PRODUCER_FILENAME
        if not injected and target.exists():
            replacement = target.with_name("replacement.whl")
            replacement.write_bytes(b"attacker replacement")
            os.replace(replacement, target)
            injected = True
        real_utime(*arguments, **keywords)

    monkeypatch.setattr(module_globals["os"], "utime", replacing_utime)

    with pytest.raises(error_type, match="changed|identity|output"):
        module_globals["_run"](source, build_python, work_root)

    assert injected
    assert not (work_root / "wheel-b" / PRODUCER_FILENAME).exists()


@pytest.fixture(scope="module")
def producer_wheel_pair(
    tmp_path_factory: pytest.TempPathFactory,
    build_python: Path,
) -> tuple[Path, Path]:
    work_root = prepare_producer_work_root(
        tmp_path_factory.mktemp("producer-wheel-pair") / "work"
    )
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


def test_build_seal_rechecks_the_authenticated_source_through_publication(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
) -> None:
    source = _copy_seal_source_root(tmp_path / "analysis-copy")
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    producer_lock = tmp_path / "producer.lock"
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerSealError"]
    real_authenticate_locks = module_globals["_authenticate_locks"]
    target = source / "equipment_quality/__init__.py"
    mutated = False

    def mutating_authenticate_locks(path: Path) -> dict[str, bytes]:
        nonlocal mutated
        locks = real_authenticate_locks(path)
        target.write_bytes(target.read_bytes() + b"# late mutation\n")
        mutated = True
        return locks

    module_globals["_authenticate_locks"] = mutating_authenticate_locks

    with pytest.raises(error_type, match="source|changed|inventory|identity"):
        module_globals["_run"](
            source,
            wheel_a.parent,
            wheel_b.parent,
            wheelhouse,
            producer_lock,
        )

    assert mutated
    assert not (wheelhouse / PRODUCER_FILENAME).exists()
    assert not producer_lock.exists()
    assert not list(tmp_path.rglob(".sfep-producer-*"))


def test_publication_normalizes_output_modes_under_a_restrictive_umask(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    lock_parent = tmp_path / "lock-parent"
    lock_parent.mkdir()
    producer_lock = lock_parent / "producer.lock"
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    previous_umask = os.umask(0o777)
    try:
        namespace["_run"](
            ANALYSIS_ROOT,
            wheel_a.parent,
            wheel_b.parent,
            wheelhouse,
            producer_lock,
        )
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(os.lstat(wheelhouse / PRODUCER_FILENAME).st_mode) == 0o644
    assert stat.S_IMODE(os.lstat(producer_lock).st_mode) == 0o644
    assert not list(tmp_path.rglob(".sfep-producer-*"))


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
        ("local-timestamp", "local"),
        ("local-method", "local"),
        ("local-crc", "local"),
        ("local-compressed-size", "local"),
        ("local-uncompressed-size", "local"),
        ("local-data-descriptor", "local"),
        ("local-encryption", "local"),
        ("unclaimed-gap", "unclaimed"),
        ("eocd-disk", "disk"),
        ("eocd-central-size", "unclaimed"),
        ("central-needed-version", "version"),
        ("central-internal-attribute", "attribute"),
        ("central-external-low-bits", "attribute"),
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
    "case",
    (
        "lock-in-wheelhouse",
        "lock-in-wheel-a",
        "lock-equals-wheel-b",
        "lock-in-source-package",
        "wheelhouse-is-wheel-a",
        "wheelhouse-under-wheel-a",
        "wheelhouse-under-case-aliased-wheel-a",
    ),
)
def test_build_seal_rejects_overlapping_input_and_lock_paths_before_mutation(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    case: str,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    source = ANALYSIS_ROOT
    producer_lock = tmp_path / "producer.lock"
    if case == "lock-in-wheelhouse":
        producer_lock = wheelhouse / "producer.lock"
    elif case == "lock-in-wheel-a":
        producer_lock = wheel_a.parent / "producer.lock"
    elif case == "lock-equals-wheel-b":
        producer_lock = wheel_b.parent
    elif case == "lock-in-source-package":
        source = _copy_seal_source_root(tmp_path / "analysis-copy")
        producer_lock = source / "equipment_quality/producer.lock"
    elif case == "wheelhouse-is-wheel-a":
        wheelhouse = wheel_a.parent
    elif case == "wheelhouse-under-wheel-a":
        wheelhouse = _copy_third_party_wheelhouse(wheel_a.parent / "wheelhouse")
    elif case == "wheelhouse-under-case-aliased-wheel-a":
        aliased_wheel_dir = wheel_a.parent.with_name(wheel_a.parent.name.swapcase())
        if not aliased_wheel_dir.exists() or not os.path.samefile(
            aliased_wheel_dir,
            wheel_a.parent,
        ):
            pytest.skip("requires a case-insensitive filesystem")
        wheelhouse = _copy_third_party_wheelhouse(aliased_wheel_dir / "wheelhouse")
    wheel_a_before = wheel_a.read_bytes()
    wheel_b_before = wheel_b.read_bytes()
    wheelhouse_before = sorted(path.name for path in wheelhouse.iterdir())

    result = run_build_seal(
        source,
        wheel_a.parent,
        wheel_b.parent,
        wheelhouse,
        producer_lock,
    )

    assert result.returncode != 0
    assert "disjoint" in result.stderr.casefold()
    assert wheel_a.read_bytes() == wheel_a_before
    assert wheel_b.read_bytes() == wheel_b_before
    assert sorted(path.name for path in wheelhouse.iterdir()) == wheelhouse_before
    assert not list(tmp_path.rglob(".sfep-producer-*"))


def test_build_seal_allows_the_normal_lock_inside_the_source_root(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
) -> None:
    source = _copy_seal_source_root(tmp_path / "analysis-copy")
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    producer_lock = source / "producer.lock"

    result = run_build_seal(
        source,
        wheel_a.parent,
        wheel_b.parent,
        wheelhouse,
        producer_lock,
    )

    assert result.returncode == 0, result.stderr
    assert producer_lock.read_bytes() == _expected_producer_lock(wheel_a)
    assert (wheelhouse / PRODUCER_FILENAME).read_bytes() == wheel_a.read_bytes()


@pytest.mark.parametrize(
    "case",
    (
        "wheel-a-directory-replacement",
        "wheelhouse-directory-replacement",
        "lock-parent-directory-replacement",
        "wheel-a-addition",
        "wheel-b-file-replacement",
        "wheelhouse-file-replacement",
        "lock-parent-addition",
    ),
)
def test_build_seal_rechecks_directory_pins_immediately_before_publication(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    case: str,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    lock_parent = tmp_path / "lock-parent"
    lock_parent.mkdir()
    producer_lock = lock_parent / "producer.lock"
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerSealError"]
    real_preflight = module_globals["_preflight_outputs"]
    mutated = False

    def replacing_preflight(*arguments: object) -> bool:
        nonlocal mutated
        result = real_preflight(*arguments)
        if case.endswith("directory-replacement"):
            target = {
                "wheel-a-directory-replacement": wheel_a.parent,
                "wheelhouse-directory-replacement": wheelhouse,
                "lock-parent-directory-replacement": lock_parent,
            }[case]
            original = target.with_name(target.name + "-original")
            target.rename(original)
            shutil.copytree(original, target)
        elif case == "wheel-a-addition":
            (wheel_a.parent / "late.whl").write_bytes(b"late")
        elif case == "wheel-b-file-replacement":
            replacement = wheel_b.with_name("replacement.whl")
            shutil.copy2(wheel_b, replacement)
            os.replace(replacement, wheel_b)
        elif case == "wheelhouse-file-replacement":
            target = wheelhouse / THIRD_PARTY_WHEELS[0][2]
            replacement = wheelhouse / "replacement.whl"
            shutil.copy2(target, replacement)
            os.replace(replacement, target)
        else:
            (lock_parent / "late").write_bytes(b"late")
        mutated = True
        return result

    module_globals["_preflight_outputs"] = replacing_preflight

    with pytest.raises(error_type, match="changed|identity|inventory|physical"):
        module_globals["_run"](
            ANALYSIS_ROOT,
            wheel_a.parent,
            wheel_b.parent,
            wheelhouse,
            producer_lock,
        )

    assert mutated
    assert not producer_lock.exists()
    assert not (wheelhouse / PRODUCER_FILENAME).exists()


@pytest.mark.parametrize(
    "case",
    ("wheelhouse", "lock-parent", "wheel-a-addition", "wheel-b-file-replacement"),
)
def test_build_seal_binds_publication_to_the_verified_parent_directories(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    case: str,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    lock_parent = tmp_path / "lock-parent"
    lock_parent.mkdir()
    producer_lock = lock_parent / "producer.lock"
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerSealError"]
    real_publish = module_globals["_publish_no_clobber"]
    target = {
        "wheelhouse": wheelhouse,
        "lock-parent": lock_parent,
    }.get(case)
    original = (
        target.with_name(target.name + "-original")
        if target is not None
        else None
    )
    swapped = False

    def swapping_publish(*arguments: object, **keywords: object) -> None:
        nonlocal swapped
        if case in {"wheelhouse", "lock-parent"}:
            assert target is not None and original is not None
            target.rename(original)
            shutil.copytree(original, target)
        elif case == "wheel-a-addition":
            (wheel_a.parent / "late.whl").write_bytes(b"late")
        else:
            replacement = wheel_b.with_name("replacement.whl")
            shutil.copy2(wheel_b, replacement)
            os.replace(replacement, wheel_b)
        swapped = True
        real_publish(*arguments, **keywords)

    module_globals["_publish_no_clobber"] = swapping_publish

    with pytest.raises(error_type, match="changed|identity|physical"):
        module_globals["_run"](
            ANALYSIS_ROOT,
            wheel_a.parent,
            wheel_b.parent,
            wheelhouse,
            producer_lock,
        )

    assert swapped
    assert not (wheelhouse / PRODUCER_FILENAME).exists()
    if case == "wheelhouse":
        assert original is not None
        assert not (original / PRODUCER_FILENAME).exists()
    assert not producer_lock.exists()
    if case == "lock-parent":
        assert original is not None
        assert not (original / "producer.lock").exists()
    assert not list(tmp_path.rglob(".sfep-producer-*"))


def test_build_seal_rechecks_authenticated_outputs_before_reporting_reuse(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    producer_lock = tmp_path / "producer.lock"
    created = run_build_seal(
        ANALYSIS_ROOT,
        wheel_a.parent,
        wheel_b.parent,
        wheelhouse,
        producer_lock,
    )
    assert created.returncode == 0, created.stderr
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerSealError"]
    real_preflight = module_globals["_preflight_outputs"]
    published = wheelhouse / PRODUCER_FILENAME
    replaced = False

    def replacing_preflight(*arguments: object) -> bool:
        nonlocal replaced
        result = real_preflight(*arguments)
        replacement = wheelhouse / "replacement.whl"
        replacement.write_bytes(b"stale")
        os.replace(replacement, published)
        replaced = True
        return result

    module_globals["_preflight_outputs"] = replacing_preflight

    with pytest.raises(error_type, match="changed|identity|inventory"):
        module_globals["_run"](
            ANALYSIS_ROOT,
            wheel_a.parent,
            wheel_b.parent,
            wheelhouse,
            producer_lock,
        )

    assert replaced
    assert published.read_bytes() == b"stale"
    assert producer_lock.read_bytes() == _expected_producer_lock(wheel_a)
    assert not list(tmp_path.rglob(".sfep-producer-*"))


@pytest.mark.parametrize("case", ("wheel-temp", "lock-temp"))
def test_publication_rejects_a_temp_inode_mutated_after_link(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    lock_parent = tmp_path / "lock-parent"
    lock_parent.mkdir()
    producer_lock = lock_parent / "producer.lock"
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerSealError"]
    real_link = os.link
    calls = 0

    def mutating_link(
        source_name: str,
        target_name: str,
        *arguments: object,
        **keywords: object,
    ) -> None:
        nonlocal calls
        real_link(source_name, target_name, *arguments, **keywords)
        calls += 1
        expected_call = 1 if case == "wheel-temp" else 2
        if calls == expected_call:
            descriptor = os.open(
                source_name,
                os.O_WRONLY | os.O_TRUNC,
                dir_fd=keywords["src_dir_fd"],
            )
            try:
                os.write(descriptor, b"corrupt")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    monkeypatch.setattr(module_globals["os"], "link", mutating_link)

    with pytest.raises(error_type, match="changed|authenticated|payload|temporary"):
        module_globals["_run"](
            ANALYSIS_ROOT,
            wheel_a.parent,
            wheel_b.parent,
            wheelhouse,
            producer_lock,
        )

    assert calls >= (1 if case == "wheel-temp" else 2)
    assert not (wheelhouse / PRODUCER_FILENAME).exists()
    assert not producer_lock.exists()
    assert not list(tmp_path.rglob(".sfep-producer-*"))


def test_publication_rollback_does_not_delete_a_replaced_target(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    lock_parent = tmp_path / "lock-parent"
    lock_parent.mkdir()
    producer_lock = lock_parent / "producer.lock"
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerSealError"]
    real_link = os.link
    calls = 0
    wheelhouse_descriptor: int | None = None

    def replacing_link(
        source_name: str,
        target_name: str,
        *arguments: object,
        **keywords: object,
    ) -> None:
        nonlocal calls, wheelhouse_descriptor
        real_link(source_name, target_name, *arguments, **keywords)
        calls += 1
        if calls == 1:
            wheelhouse_descriptor = int(keywords["dst_dir_fd"])
        elif calls == 2:
            assert wheelhouse_descriptor is not None
            external = ".external-replacement"
            descriptor = os.open(
                external,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o644,
                dir_fd=wheelhouse_descriptor,
            )
            try:
                os.write(descriptor, b"external")
            finally:
                os.close(descriptor)
            os.replace(
                external,
                PRODUCER_FILENAME,
                src_dir_fd=wheelhouse_descriptor,
                dst_dir_fd=wheelhouse_descriptor,
            )

    monkeypatch.setattr(module_globals["os"], "link", replacing_link)

    with pytest.raises(error_type, match="changed|collision|identity"):
        module_globals["_run"](
            ANALYSIS_ROOT,
            wheel_a.parent,
            wheel_b.parent,
            wheelhouse,
            producer_lock,
        )

    assert (wheelhouse / PRODUCER_FILENAME).read_bytes() == b"external"
    assert not producer_lock.exists()
    assert not list(tmp_path.rglob(".sfep-producer-*"))


@pytest.mark.parametrize("case", ("wheel-content", "wheelhouse-swap"))
def test_publication_rechecks_paths_and_payloads_after_temp_cleanup(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    case: str,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    lock_parent = tmp_path / "lock-parent"
    lock_parent.mkdir()
    producer_lock = lock_parent / "producer.lock"
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerSealError"]
    real_unlink_owned = module_globals["_unlink_owned_name"]
    original_wheelhouse = tmp_path / "wheelhouse-original"
    injected = False

    def injecting_unlink(
        parent_descriptor: int,
        name: str,
        owner_descriptor: int,
    ) -> bool:
        nonlocal injected
        result = real_unlink_owned(parent_descriptor, name, owner_descriptor)
        if not injected and name.startswith(".sfep-producer-"):
            if case == "wheel-content":
                descriptor = os.open(
                    PRODUCER_FILENAME,
                    os.O_WRONLY | os.O_TRUNC,
                    dir_fd=parent_descriptor,
                )
                try:
                    os.write(descriptor, b"corrupt after cleanup")
                finally:
                    os.close(descriptor)
            else:
                wheelhouse.rename(original_wheelhouse)
                shutil.copytree(original_wheelhouse, wheelhouse)
            injected = True
        return result

    module_globals["_unlink_owned_name"] = injecting_unlink

    with pytest.raises(error_type, match="changed|identity|physical|payload"):
        module_globals["_run"](
            ANALYSIS_ROOT,
            wheel_a.parent,
            wheel_b.parent,
            wheelhouse,
            producer_lock,
        )

    assert injected
    if case == "wheel-content":
        assert not (wheelhouse / PRODUCER_FILENAME).exists()
    else:
        assert not (original_wheelhouse / PRODUCER_FILENAME).exists()
    assert not producer_lock.exists()
    assert not list(tmp_path.rglob(".sfep-producer-*"))


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


@pytest.mark.parametrize("failure", ("second-mkstemp", "second-write", "second-fsync"))
def test_publication_cleans_both_temps_when_the_second_temp_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    lock_parent = tmp_path / "lock-parent"
    wheelhouse.mkdir()
    lock_parent.mkdir()
    target_wheel = wheelhouse / PRODUCER_FILENAME
    producer_lock = lock_parent / "producer.lock"
    namespace = runpy.run_path(str(TOOLS_ROOT / "seal_producer_build.py"))
    module_globals = namespace["_publish_no_clobber"].__globals__
    calls = 0
    if failure == "second-mkstemp":
        real_operation = tempfile.mkstemp

        def failing_operation(*arguments: object, **keywords: object):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("injected second mkstemp failure")
            return real_operation(*arguments, **keywords)

        monkeypatch.setattr(module_globals["tempfile"], "mkstemp", failing_operation)
    elif failure == "second-write":
        real_operation = os.write

        def failing_operation(*arguments: object, **keywords: object):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second write failure")
            return real_operation(*arguments, **keywords)

        monkeypatch.setattr(module_globals["os"], "write", failing_operation)
    else:
        real_operation = os.fsync

        def failing_operation(*arguments: object, **keywords: object):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second fsync failure")
            return real_operation(*arguments, **keywords)

        monkeypatch.setattr(module_globals["os"], "fsync", failing_operation)

    with pytest.raises(OSError, match="injected"):
        module_globals["_publish_no_clobber"](
            target_wheel,
            producer_lock,
            b"wheel bytes",
            b"lock bytes\n",
        )

    assert calls >= 2
    assert not target_wheel.exists()
    assert not producer_lock.exists()
    assert not list(wheelhouse.glob(".sfep-producer-*"))
    assert not list(lock_parent.glob(".sfep-producer-*"))


@pytest.mark.parametrize("case", ("read-only", "missing", "file-parent"))
def test_build_seal_bad_lock_parent_leaves_no_temp_or_output_residue(
    tmp_path: Path,
    producer_wheel_pair: tuple[Path, Path],
    case: str,
) -> None:
    wheel_a, wheel_b = _copy_wheel_pair(tmp_path / "pair", producer_wheel_pair)
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    lock_parent = tmp_path / "lock-parent"
    if case == "read-only":
        lock_parent.mkdir(mode=0o555)
    elif case == "file-parent":
        lock_parent.write_bytes(b"not a directory\n")
    producer_lock = lock_parent / "producer.lock"
    wheelhouse_before = sorted(path.name for path in wheelhouse.iterdir())

    try:
        result = run_build_seal(
            ANALYSIS_ROOT,
            wheel_a.parent,
            wheel_b.parent,
            wheelhouse,
            producer_lock,
        )
    finally:
        if case == "read-only":
            lock_parent.chmod(0o755)

    assert result.returncode != 0
    assert sorted(path.name for path in wheelhouse.iterdir()) == wheelhouse_before
    assert not producer_lock.exists()
    assert not list(tmp_path.rglob(".sfep-producer-*"))


# Task 9c: independent installed-runtime seal ---------------------------------

RUNTIME_PACKAGE_NAMES = (
    "attrs",
    "jsonschema",
    "jsonschema-specifications",
    "numpy",
    "pandas",
    "python-dateutil",
    "pytz",
    "referencing",
    "rpds-py",
    "six",
    "typing-extensions",
    "tzdata",
)
RUNTIME_DIRECT_NAMES = frozenset({"jsonschema", "numpy", "pandas"})
RUNTIME_HASH_PROBES = (-4218979432691865272, 1379760580859628941)
RUNTIME_GENERATED_SCRIPTS = {
    "jsonschema": {"jsonschema": "jsonschema.cli:main"},
    "numpy": {
        "f2py": "numpy.f2py.f2py2e:main",
        "numpy-config": "numpy._configtool:main",
    },
    "pip": {
        "pip": "pip._internal.cli.main:main",
        "pip3": "pip._internal.cli.main:main",
        "pip3.12": "pip._internal.cli.main:main",
    },
    "sfep-equipment-quality": {
        "sfep-equipment-quality": "equipment_quality.cli:main"
    },
}


def _runtime_console_wrapper(executable: Path, target: str) -> bytes:
    module, function = target.split(":", 1)
    return (
        f"#!{executable}\n"
        "# -*- coding: utf-8 -*-\n"
        "import re\n"
        "import sys\n"
        f"from {module} import {function}\n"
        "if __name__ == '__main__':\n"
        "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
        f"    sys.exit({function}())\n"
    ).encode("utf-8")


def _write_runtime_pyvenv_cfg(runtime: Path, executable: Path) -> None:
    runtime.joinpath("pyvenv.cfg").write_text(
        "home = " + str(executable.parent) + "\n"
        "include-system-site-packages = false\n"
        "version = 3.12.10\n"
        "executable = " + str(executable) + "\n"
        "command = test oracle\n",
        encoding="utf-8",
    )


def _extract_runtime_wheel(wheel: Path, purelib: Path) -> None:
    """Install the immutable wheel payload for a test-side RECORD oracle."""
    with zipfile.ZipFile(io.BytesIO(wheel.read_bytes())) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            target = purelib.joinpath(*PurePosixPath(info.orig_filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))


def _prepare_runtime_seal_fixture(root: Path) -> dict[str, object]:
    venv = root / "runtime-venv"
    purelib = venv / "lib/python3.12/site-packages"
    purelib.mkdir(parents=True)
    executable = venv / "bin/python"
    executable.parent.mkdir()
    shutil.copy2(Path(os.path.realpath(sys.executable)), executable)
    _write_runtime_pyvenv_cfg(venv, executable)
    runtime_specs = [
        item for item in THIRD_PARTY_WHEELS if item[0] in RUNTIME_PACKAGE_NAMES
    ]
    pip_spec = next(item for item in THIRD_PARTY_WHEELS if item[0] == "pip")
    for spec in (*runtime_specs, pip_spec):
        _extract_runtime_wheel(WHEELHOUSE / spec[2], purelib)
    _extract_runtime_wheel(WHEELHOUSE / PRODUCER_FILENAME, purelib)
    for dist_info in sorted(purelib.glob("*.dist-info")):
        metadata = (dist_info / "METADATA").read_text(encoding="utf-8")
        distribution_name = next(
            line.removeprefix("Name: ").strip().casefold().replace("_", "-")
            for line in metadata.splitlines()
            if line.startswith("Name: ")
        )
        record = dist_info / "RECORD"
        rows = _record_rows(record.read_bytes())
        additions = {
            f"{dist_info.name}/INSTALLER": b"pip\n",
            f"{dist_info.name}/REQUESTED": b"",
        }
        if distribution_name == "sfep-equipment-quality":
            additions[f"{dist_info.name}/direct_url.json"] = (
                b'{"url":"file://'
                + str(root).encode("utf-8")
                + b'/absolute-wheelhouse"}\n'
            )
        for relative, payload in additions.items():
            target = purelib.joinpath(*PurePosixPath(relative).parts)
            target.write_bytes(payload)
            rows.insert(-1, [relative, _record_digest(payload), str(len(payload))])
        for script_name, target_name in RUNTIME_GENERATED_SCRIPTS.get(
            distribution_name, {}
        ).items():
            relative = f"../../../bin/{script_name}"
            payload = _runtime_console_wrapper(executable, target_name)
            target = venv / "bin" / script_name
            target.write_bytes(payload)
            target.chmod(0o755)
            rows.insert(-1, [relative, _record_digest(payload), str(len(payload))])
        record.write_bytes(_render_record(rows))
    probe = {
        "schemaVersion": "sfep-runtime-probe/v1",
        "prefix": str(venv),
        "executable": str(executable),
        "purelib": str(purelib),
        "platlib": str(purelib),
        "flags": {
            "isolated": 1,
            "noSite": 1,
            "ignoreEnvironment": 1,
            "safePath": True,
        },
        "platform": {
            "system": "Darwin",
            "machine": "arm64",
            "macosProductVersion": "26.6.2",
            "sysconfigPlatform": "macosx-11.0-arm64",
        },
        "python": {
            "implementation": "CPython",
            "version": "3.12.10",
            "build": "main Apr 10 2025 22:19:24",
            "cacheTag": "cpython-312",
            "soabi": "cpython-312-darwin",
        },
        "environment": {"pythonHashSeed": "0", "timezone": "Asia/Seoul"},
        "hashProbes": list(RUNTIME_HASH_PROBES),
    }
    return {
        "venv": venv,
        "purelib": purelib,
        "executable": executable,
        "probe": probe,
    }


@pytest.fixture(scope="session")
def runtime_seal_fixture(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    return _prepare_runtime_seal_fixture(
        tmp_path_factory.mktemp("runtime-seal-independent")
    )


def _runtime_seal_namespace() -> dict[str, object]:
    return runpy.run_path(str(TOOLS_ROOT / "seal_runtime.py"))


def _run_runtime_seal_in_process(
    runtime_fixture: dict[str, object],
    output: Path,
    *,
    source_root: Path = ANALYSIS_ROOT,
    producer_wheel_dir: Path = WHEELHOUSE,
    producer_lock: Path | None = None,
) -> bytes:
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_run"].__globals__
    probe = copy.deepcopy(runtime_fixture["probe"])
    module_globals["_probe_runtime"] = lambda *_arguments: probe
    module_globals["_run"](
        source_root,
        producer_wheel_dir,
        ANALYSIS_ROOT / "producer.lock" if producer_lock is None else producer_lock,
        runtime_fixture["venv"],
        output,
    )
    return output.read_bytes()


def _independent_record_tree(purelib: Path, dist_info: Path) -> str:
    rows = _record_rows((dist_info / "RECORD").read_bytes())
    lines: list[tuple[bytes, bytes]] = []
    for path, hash_field, _size in rows:
        parts = PurePosixPath(path).parts
        generated_script = (
            len(parts) == 5 and parts[:4] == ("..", "..", "..", "bin")
        )
        excluded_metadata = (
            len(parts) >= 2
            and parts[-2].endswith(".dist-info")
            and parts[-1] in {"RECORD", "INSTALLER", "direct_url.json", "REQUESTED"}
        )
        if (
            generated_script
            or "__pycache__" in parts
            or path.endswith(".pyc")
            or excluded_metadata
        ):
            continue
        assert hash_field.startswith("sha256=")
        target = purelib.joinpath(*parts)
        encoded = path.encode("utf-8")
        lines.append(
            (
                encoded,
                encoded
                + b"=sha256:"
                + hashlib.sha256(target.read_bytes()).hexdigest().encode("ascii")
                + b"\n",
            )
        )
    preimage = b"".join(line for _path, line in sorted(lines, key=lambda item: item[0]))
    return _sha256_uri(preimage)


def _record_digest(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return "sha256=" + encoded.decode("ascii")


def _runtime_tool_error() -> type[Exception]:
    namespace = _runtime_seal_namespace()
    error = namespace["RuntimeSealError"]
    assert isinstance(error, type) and issubclass(error, Exception)
    return error


def test_runtime_seal_offers_exact_cli_and_imports_only_stdlib(tmp_path: Path) -> None:
    result = _run_tool("seal_runtime.py", "--help", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "--source-root" in result.stdout
    assert "--producer-wheel-dir" in result.stdout
    assert "--producer-lock" in result.stdout
    assert "--runtime-venv" in result.stdout
    assert "--output" in result.stdout
    tree = ast.parse((TOOLS_ROOT / "seal_runtime.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.partition(".")[0])
    assert "equipment_quality" not in imported
    assert imported <= sys.stdlib_module_names


def test_runtime_seal_rejects_undocumented_cli_forms_before_work(
    tmp_path: Path,
) -> None:
    exact = [
        "--source-root",
        str(tmp_path / "source"),
        "--producer-wheel-dir",
        str(tmp_path / "wheelhouse"),
        "--producer-lock",
        str(tmp_path / "producer.lock"),
        "--runtime-venv",
        str(tmp_path / "venv"),
        "--output",
        str(tmp_path / "runtime.json"),
    ]
    cases = (
        ["--source", *exact[1:]],
        [*exact, "unexpected"],
        [*exact[:1], "relative", *exact[2:]],
    )
    for arguments in cases:
        result = _run_tool("seal_runtime.py", *arguments, cwd=tmp_path)
        assert result.returncode == 2
        assert "Traceback" not in result.stderr
        assert not (tmp_path / "runtime.json").exists()


def test_runtime_seal_is_canonical_path_independent_and_schema_valid(
    tmp_path: Path,
    runtime_seal_fixture: dict[str, object],
) -> None:
    second_fixture = _prepare_runtime_seal_fixture(tmp_path / "path-distinct-install")
    first = _run_runtime_seal_in_process(
        runtime_seal_fixture, tmp_path / "first-runtime.json"
    )
    second = _run_runtime_seal_in_process(
        second_fixture, tmp_path / "second-runtime.json"
    )

    assert first == second
    manifest = json.loads(first)
    assert first == _canonical_json_bytes(manifest)
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (ANALYSIS_ROOT / "equipment_quality/contracts/v1/producer_runtime.schema.json")
        .read_bytes()
    )
    Draft202012Validator(schema).validate(manifest)
    assert [item["name"] for item in manifest["packages"]] == list(
        RUNTIME_PACKAGE_NAMES
    )
    assert [item["direct"] for item in manifest["packages"]] == [
        name in RUNTIME_DIRECT_NAMES for name in RUNTIME_PACKAGE_NAMES
    ]
    assert manifest["pipVersion"] == "25.1.1"
    assert manifest["producer"]["name"] == "equipment-quality"
    assert manifest["producer"]["version"] == "1.0.0"
    forbidden = {
        str(ANALYSIS_ROOT).encode(),
        str(WHEELHOUSE).encode(),
        str(runtime_seal_fixture["venv"]).encode(),
        str(second_fixture["venv"]).encode(),
        str(tmp_path).encode(),
    }
    assert all(token not in first for token in forbidden)


@pytest.mark.parametrize(
    "case",
    (
        "source",
        "pyproject",
        "bootstrap",
        "build-requirements",
        "requirements",
        "wheelhouse-lock",
        "producer-lock",
        "third-party-wheel",
        "producer-wheel",
        "extra-wheel",
        "missing-wheel",
    ),
)
def test_runtime_seal_rejects_authenticated_input_drift(
    tmp_path: Path,
    runtime_seal_fixture: dict[str, object],
    case: str,
) -> None:
    source = _copy_seal_source_root(tmp_path / "source")
    wheelhouse = _copy_third_party_wheelhouse(tmp_path / "wheelhouse")
    shutil.copy2(WHEELHOUSE / PRODUCER_FILENAME, wheelhouse / PRODUCER_FILENAME)
    producer_lock = tmp_path / "producer.lock"
    shutil.copy2(ANALYSIS_ROOT / "producer.lock", producer_lock)
    if case == "source":
        target = source / "equipment_quality/__init__.py"
        target.write_bytes(target.read_bytes() + b"# drift\n")
    elif case == "pyproject":
        target = source / "pyproject.toml"
        target.write_bytes(target.read_bytes() + b"\n")
    elif case in {"bootstrap", "build-requirements", "requirements"}:
        filename = {
            "bootstrap": "bootstrap.lock",
            "build-requirements": "build-requirements.lock",
            "requirements": "requirements.lock",
        }[case]
        target = source / filename
        target.write_bytes(target.read_bytes().replace(b"sha256:", b"sha256:0", 1))
    elif case == "wheelhouse-lock":
        target = source / "wheelhouse.lock.json"
        target.write_bytes(target.read_bytes().replace(b'"tag":', b'"tag" :', 1))
    elif case == "producer-lock":
        producer_lock.write_bytes(producer_lock.read_bytes().replace(b"sha256:", b"sha256:0"))
    elif case == "third-party-wheel":
        target = wheelhouse / THIRD_PARTY_WHEELS[0][2]
        target.write_bytes(target.read_bytes() + b"drift")
    elif case == "producer-wheel":
        target = wheelhouse / PRODUCER_FILENAME
        target.write_bytes(target.read_bytes() + b"drift")
    elif case == "extra-wheel":
        (wheelhouse / "extra-1.0.0-py3-none-any.whl").write_bytes(b"extra")
    elif case == "missing-wheel":
        (wheelhouse / THIRD_PARTY_WHEELS[0][2]).unlink()

    with pytest.raises(RuntimeError):
        _run_runtime_seal_in_process(
            runtime_seal_fixture,
            tmp_path / "runtime.json",
            source_root=source,
            producer_wheel_dir=wheelhouse,
            producer_lock=producer_lock,
        )
    assert not (tmp_path / "runtime.json").exists()


def _tiny_installed_distribution(root: Path) -> tuple[Path, Path, str]:
    purelib = root / "venv/lib/python3.12/site-packages"
    package = purelib / "demo/__init__.py"
    metadata = purelib / "demo-1.0.dist-info/METADATA"
    wheel = purelib / "demo-1.0.dist-info/WHEEL"
    package.parent.mkdir(parents=True)
    metadata.parent.mkdir(parents=True)
    package.write_bytes(b"VALUE = 1\n")
    metadata.write_bytes(b"Metadata-Version: 2.4\nName: demo\nVersion: 1.0\n\n")
    wheel.write_bytes(b"Wheel-Version: 1.0\nTag: py3-none-any\n\n")
    rows = [
        ["demo/__init__.py", _record_digest(package.read_bytes()), str(package.stat().st_size)],
        ["demo-1.0.dist-info/METADATA", _record_digest(metadata.read_bytes()), str(metadata.stat().st_size)],
        ["demo-1.0.dist-info/WHEEL", _record_digest(wheel.read_bytes()), str(wheel.stat().st_size)],
        ["demo-1.0.dist-info/RECORD", "", ""],
    ]
    record = metadata.parent / "RECORD"
    record.write_bytes(_render_record(rows))
    expected = _independent_record_tree(purelib, metadata.parent)
    return purelib, metadata.parent, expected


@pytest.mark.parametrize(
    "case",
    (
        "hash",
        "size",
        "duplicate",
        "traversal",
        "control",
        "missing-hash",
        "missing-self",
        "symlink",
        "hardlink",
        "tree-drift",
        "metadata-version",
        "metadata-malformed",
        "record-malformed",
        "absolute",
        "algorithm",
    ),
)
def test_runtime_seal_rejects_record_and_installed_tree_mutations(
    tmp_path: Path,
    case: str,
) -> None:
    purelib, dist_info, expected = _tiny_installed_distribution(tmp_path)
    record = dist_info / "RECORD"
    rows = _record_rows(record.read_bytes())
    package = purelib / "demo/__init__.py"
    if case == "hash":
        rows[0][1] = "sha256=" + "A" * 43
    elif case == "size":
        rows[0][2] = str(int(rows[0][2]) + 1)
    elif case == "duplicate":
        rows.insert(1, list(rows[0]))
    elif case == "traversal":
        rows[0][0] = "../escape.py"
    elif case == "control":
        rows[0][0] = "demo/bad\x01.py"
    elif case == "missing-hash":
        rows[0][1:] = ["", ""]
    elif case == "missing-self":
        rows.pop()
    elif case == "symlink":
        external = tmp_path / "external.py"
        external.write_bytes(package.read_bytes())
        package.unlink()
        package.symlink_to(external)
    elif case == "hardlink":
        alias = purelib / "demo/alias.py"
        os.link(package, alias)
        rows.insert(1, ["demo/alias.py", rows[0][1], rows[0][2]])
    elif case == "tree-drift":
        package.write_bytes(b"VALUE = 2\n")
        rows[0][1] = _record_digest(package.read_bytes())
        rows[0][2] = str(package.stat().st_size)
    elif case == "metadata-version":
        metadata = dist_info / "METADATA"
        metadata.write_bytes(
            metadata.read_bytes().replace(b"Version: 1.0", b"Version: 2.0")
        )
    elif case == "metadata-malformed":
        (dist_info / "METADATA").write_bytes(b"Name\x00: demo\nVersion: 1.0\n")
    elif case == "record-malformed":
        record.write_bytes(b'"unterminated\n')
    elif case == "absolute":
        rows[0][0] = "/absolute.py"
    elif case == "algorithm":
        rows[0][1] = "md5=" + "A" * 43
    if case != "record-malformed":
        record.write_bytes(_render_record(rows))
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_verify_installed_distribution"].__globals__
    error_type = namespace["RuntimeSealError"]

    with pytest.raises(error_type):
        module_globals["_verify_installed_distribution"](
            purelib,
            dist_info,
            "demo",
            "1.0",
            expected,
            None,
            {},
        )


@pytest.mark.parametrize(
    ("case", "field", "value"),
    (
        ("system", ("platform", "system"), "Linux"),
        ("machine", ("platform", "machine"), "x86_64"),
        ("version", ("python", "version"), "3.12.9"),
        ("implementation", ("python", "implementation"), "PyPy"),
        ("cache-tag", ("python", "cacheTag"), "cpython-311"),
        ("soabi", ("python", "soabi"), "cpython-311-darwin"),
        ("hash-seed", ("environment", "pythonHashSeed"), "1"),
        ("timezone", ("environment", "timezone"), "UTC"),
        ("hash-probes", ("hashProbes",), [0, 0]),
    ),
)
def test_runtime_seal_rejects_wrong_runtime_probe_facts(
    tmp_path: Path,
    runtime_seal_fixture: dict[str, object],
    case: str,
    field: tuple[str, ...],
    value: object,
) -> None:
    del case
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_run"].__globals__
    probe = copy.deepcopy(runtime_seal_fixture["probe"])
    target: object = probe
    for member in field[:-1]:
        assert isinstance(target, dict)
        target = target[member]
    assert isinstance(target, dict)
    target[field[-1]] = value
    module_globals["_probe_runtime"] = lambda *_arguments: probe

    with pytest.raises(namespace["RuntimeSealError"]):
        module_globals["_run"](
            ANALYSIS_ROOT,
            WHEELHOUSE,
            ANALYSIS_ROOT / "producer.lock",
            runtime_seal_fixture["venv"],
            tmp_path / "runtime.json",
        )


def test_runtime_seal_publication_is_no_clobber_and_umask_independent(
    tmp_path: Path,
) -> None:
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_publish_manifest"].__globals__
    payload = _canonical_json_bytes({"sealed": True})
    output = tmp_path / "runtime.json"
    previous = os.umask(0o777)
    try:
        assert module_globals["_publish_manifest"](output, payload) == "created"
    finally:
        os.umask(previous)
    assert output.read_bytes() == payload
    assert stat.S_IMODE(os.lstat(output).st_mode) == 0o644
    assert module_globals["_publish_manifest"](output, payload) == "reused"
    output.write_bytes(b"external\n")
    with pytest.raises(namespace["RuntimeSealError"]):
        module_globals["_publish_manifest"](output, payload)
    assert output.read_bytes() == b"external\n"
    assert not list(tmp_path.glob(".sfep-runtime-*"))


def test_runtime_seal_requires_the_exact_generated_console_script_relation(
    tmp_path: Path,
) -> None:
    purelib, dist_info, expected = _tiny_installed_distribution(tmp_path)
    script = tmp_path / "venv/bin/evil"
    script.parent.mkdir()
    script.write_bytes(b"#!/bin/sh\n")
    rows = _record_rows((dist_info / "RECORD").read_bytes())
    rows.insert(
        -1,
        [
            "../../../bin/evil",
            _record_digest(script.read_bytes()),
            str(script.stat().st_size),
        ],
    )
    (dist_info / "RECORD").write_bytes(_render_record(rows))
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_verify_installed_distribution"].__globals__

    with pytest.raises(namespace["RuntimeSealError"], match="script|console"):
        module_globals["_verify_installed_distribution"](
            purelib,
            dist_info,
            "demo",
            "1.0",
            expected,
            None,
            {},
            {},
        )


@pytest.mark.parametrize(
    "case",
    ("missing", "extra", "duplicate", "build-leakage"),
)
def test_runtime_seal_requires_exact_installed_distribution_inventory(
    tmp_path: Path,
    case: str,
) -> None:
    purelib = tmp_path / "site-packages"
    purelib.mkdir()
    names = [*RUNTIME_PACKAGE_NAMES, "pip", "sfep-equipment-quality"]
    if case == "missing":
        names.remove("attrs")
    elif case == "extra":
        names.append("other-runtime")
    elif case == "build-leakage":
        names.append("setuptools")
    for index, name in enumerate(names):
        stem = name.replace("-", "_")
        dist_info = purelib / f"{stem}-1.0-{index}.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_bytes(
            f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\n\n".encode()
        )
    if case == "duplicate":
        duplicate = purelib / "duplicate_attrs-1.0.dist-info"
        duplicate.mkdir()
        (duplicate / "METADATA").write_bytes(
            b"Metadata-Version: 2.4\nName: attrs\nVersion: 1.0\n\n"
        )
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_discover_distributions"].__globals__

    with pytest.raises(namespace["RuntimeSealError"], match="inventory|duplicated|ambiguous"):
        module_globals["_discover_distributions"](purelib, purelib)


@pytest.mark.parametrize(
    "case",
    (
        "stderr-noise",
        "malformed",
        "oversized",
        "extra-member",
        "path-escape",
        "interpreter-swap",
        "timeout",
    ),
)
def test_runtime_probe_fails_closed_on_process_and_payload_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    runtime = tmp_path / "venv"
    executable = runtime / "bin/python"
    purelib = runtime / "lib/python3.12/site-packages"
    executable.parent.mkdir(parents=True)
    purelib.mkdir(parents=True)
    executable.write_bytes(b"interpreter")
    _write_runtime_pyvenv_cfg(runtime, executable)
    probe = {
        "schemaVersion": "sfep-runtime-probe/v1",
        "prefix": str(runtime),
        "executable": str(executable),
        "purelib": str(purelib),
        "platlib": str(purelib),
        "flags": {
            "isolated": 1,
            "noSite": 1,
            "ignoreEnvironment": 1,
            "safePath": True,
        },
        "platform": {
            "system": "Darwin",
            "machine": "arm64",
            "macosProductVersion": "26.6.2",
            "sysconfigPlatform": "macosx-11.0-arm64",
        },
        "python": {
            "implementation": "CPython",
            "version": "3.12.10",
            "build": "main Apr 10 2025 22:19:24",
            "cacheTag": "cpython-312",
            "soabi": "cpython-312-darwin",
        },
        "environment": {"pythonHashSeed": "0", "timezone": "Asia/Seoul"},
        "hashProbes": list(RUNTIME_HASH_PROBES),
    }
    if case == "extra-member":
        probe["extra"] = True
    elif case == "path-escape":
        probe["purelib"] = str(tmp_path / "escape")
    stdout = _canonical_json_bytes(probe)
    stderr = b""
    if case == "stderr-noise":
        stderr = b"noise\n"
    elif case == "malformed":
        stdout = b"not-json\n"
    elif case == "oversized":
        stdout = b"x" * (1024 * 1024 + 1)
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_probe_runtime"].__globals__
    if case == "timeout":
        def fake_run(*_args: object, **_kwargs: object) -> object:
            raise subprocess.TimeoutExpired("python", 20)
    else:
        def fake_run(*_args: object, **_kwargs: object) -> object:
            if case == "interpreter-swap":
                executable.write_bytes(b"replacement interpreter")
            return subprocess.CompletedProcess([], 0, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(module_globals["subprocess"], "run", fake_run)

    with pytest.raises(namespace["RuntimeSealError"]):
        value = module_globals["_probe_runtime"](runtime, executable)
        module_globals["_validate_probe"](value, runtime, executable)


@pytest.mark.parametrize("case", ("symlink-output", "write-fault", "parent-fsync"))
def test_runtime_manifest_publication_faults_leave_no_valid_owned_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_publish_manifest"].__globals__
    payload = _canonical_json_bytes({"sealed": True})
    output = tmp_path / "runtime.json"
    external = tmp_path / "external.json"
    if case == "symlink-output":
        external.write_bytes(b"external\n")
        output.symlink_to(external)
    elif case == "write-fault":
        real_write = os.write
        calls = 0

        def failing_write(*arguments: object, **keywords: object) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected write fault")
            return real_write(*arguments, **keywords)

        monkeypatch.setattr(module_globals["os"], "write", failing_write)
    else:
        real_fsync = os.fsync
        calls = 0

        def failing_fsync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected parent fsync fault")
            real_fsync(descriptor)

        monkeypatch.setattr(module_globals["os"], "fsync", failing_fsync)

    with pytest.raises((namespace["RuntimeSealError"], OSError)):
        module_globals["_publish_manifest"](output, payload)

    if case == "symlink-output":
        assert output.is_symlink()
        assert external.read_bytes() == b"external\n"
    else:
        assert output.is_file()
        assert output.read_bytes() == b""
    assert not list(tmp_path.glob(".sfep-runtime-*"))


def test_runtime_seal_rejects_output_nested_in_authenticated_mutable_inputs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    wheelhouse = source / ".wheelhouse"
    runtime = tmp_path / "venv"
    source.mkdir()
    wheelhouse.mkdir()
    runtime.mkdir()
    producer_lock = source / "producer.lock"
    producer_lock.write_bytes(b"lock\n")
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_validate_topology"].__globals__

    for output in (
        wheelhouse / "new.json",
        runtime / "new.json",
        source / "equipment_quality/new.json",
    ):
        output.parent.mkdir(exist_ok=True)
        with pytest.raises(namespace["RuntimeSealError"], match="output|overlap|nested"):
            module_globals["_validate_topology"](
                source,
                wheelhouse,
                producer_lock,
                runtime,
                output,
            )


def test_runtime_probe_rejects_a_site_path_with_an_intermediate_symlink(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "venv"
    external = tmp_path / "external-lib"
    purelib = external / "python3.12/site-packages"
    (runtime / "bin").mkdir(parents=True)
    purelib.mkdir(parents=True)
    (runtime / "lib").symlink_to(external, target_is_directory=True)
    executable = runtime / "bin/python"
    executable.write_bytes(b"interpreter")
    _write_runtime_pyvenv_cfg(runtime, executable)
    probe = {
        "schemaVersion": "sfep-runtime-probe/v1",
        "prefix": str(runtime),
        "executable": str(executable),
        "purelib": str(runtime / "lib/python3.12/site-packages"),
        "platlib": str(runtime / "lib/python3.12/site-packages"),
        "flags": {
            "isolated": 1,
            "noSite": 1,
            "ignoreEnvironment": 1,
            "safePath": True,
        },
        "platform": {
            "system": "Darwin",
            "machine": "arm64",
            "macosProductVersion": "26.6.2",
            "sysconfigPlatform": "macosx-11.0-arm64",
        },
        "python": {
            "implementation": "CPython",
            "version": "3.12.10",
            "build": "main Apr 10 2025 22:19:24",
            "cacheTag": "cpython-312",
            "soabi": "cpython-312-darwin",
        },
        "environment": {"pythonHashSeed": "0", "timezone": "Asia/Seoul"},
        "hashProbes": list(RUNTIME_HASH_PROBES),
    }
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_validate_probe"].__globals__

    with pytest.raises(namespace["RuntimeSealError"], match="symlink|physical"):
        module_globals["_validate_probe"](probe, runtime, executable)


def test_runtime_manifest_reuse_rejects_noncanonical_mode(
    tmp_path: Path,
) -> None:
    output = tmp_path / "runtime.json"
    payload = _canonical_json_bytes({"sealed": True})
    output.write_bytes(payload)
    output.chmod(0o600)
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_publish_manifest"].__globals__

    with pytest.raises(namespace["RuntimeSealError"], match="mode|permission"):
        module_globals["_publish_manifest"](output, payload)

    assert output.read_bytes() == payload
    assert stat.S_IMODE(os.lstat(output).st_mode) == 0o600


def test_runtime_probe_uses_isolation_bounded_files_and_an_allowlisted_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "venv"
    executable = runtime / "bin/python"
    purelib = runtime / "lib/python3.12/site-packages"
    executable.parent.mkdir(parents=True)
    purelib.mkdir(parents=True)
    executable.write_bytes(b"interpreter")
    _write_runtime_pyvenv_cfg(runtime, executable)
    payload = _canonical_json_bytes({
        "schemaVersion": "sfep-runtime-probe/v1",
        "prefix": str(runtime),
        "executable": str(executable),
        "purelib": str(purelib),
        "platlib": str(purelib),
        "flags": {
            "isolated": 1,
            "noSite": 1,
            "ignoreEnvironment": 1,
            "safePath": True,
        },
        "platform": {
            "system": "Darwin",
            "machine": "arm64",
            "macosProductVersion": "26.6.2",
            "sysconfigPlatform": "macosx-11.0-arm64",
        },
        "python": {
            "implementation": "CPython",
            "version": "3.12.10",
            "build": "main Apr 10 2025 22:19:24",
            "cacheTag": "cpython-312",
            "soabi": "cpython-312-darwin",
        },
        "environment": {"pythonHashSeed": "0", "timezone": "Asia/Seoul"},
        "hashProbes": list(RUNTIME_HASH_PROBES),
    })
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_probe_runtime"].__globals__
    observed = False

    def fake_run(command: list[str], **keywords: object) -> subprocess.CompletedProcess[bytes]:
        nonlocal observed
        assert command[0] == str(executable)
        assert command[1:4] == ["-I", "-S", "-c"]
        assert keywords["cwd"] == "/private/tmp"
        assert keywords["env"] == {
            "PYTHONHASHSEED": "0",
            "TZ": "Asia/Seoul",
            "LC_ALL": "C",
            "LANG": "C",
            "PATH": "/usr/bin:/bin",
        }
        assert keywords.get("capture_output") is None
        stdout = keywords["stdout"]
        stderr = keywords["stderr"]
        assert hasattr(stdout, "write") and hasattr(stderr, "write")
        stdout.write(payload)
        observed = True
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module_globals["subprocess"], "run", fake_run)

    assert module_globals["_probe_runtime"](runtime, executable)["hashProbes"] == list(
        RUNTIME_HASH_PROBES
    )
    assert observed


def test_runtime_seal_rejects_all_bytecode_and_cache_paths_before_capture(
    tmp_path: Path,
) -> None:
    purelib = tmp_path / "venv/lib/python3.12/site-packages"
    cache = purelib / "sitecustomize/__pycache__/sitecustomize.cpython-312.pyc"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"executable bytecode")
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_site_inventory"].__globals__

    with pytest.raises(namespace["RuntimeSealError"], match="bytecode|cache|pyc"):
        module_globals["_site_inventory"]((purelib,))


def test_runtime_seal_rejects_record_backed_bytecode_even_when_unhashed(
    tmp_path: Path,
) -> None:
    purelib, dist_info, expected = _tiny_installed_distribution(tmp_path)
    bytecode = purelib / "demo/__pycache__/__init__.cpython-312.pyc"
    bytecode.parent.mkdir()
    bytecode.write_bytes(b"record-backed executable bytecode")
    rows = _record_rows((dist_info / "RECORD").read_bytes())
    rows.insert(-1, ["demo/__pycache__/__init__.cpython-312.pyc", "", ""])
    (dist_info / "RECORD").write_bytes(_render_record(rows))
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_verify_installed_distribution"].__globals__

    with pytest.raises(namespace["RuntimeSealError"], match="bytecode|cache|pyc"):
        module_globals["_verify_installed_distribution"](
            purelib, dist_info, "demo", "1.0", expected, None, {}, {}
        )


@pytest.mark.parametrize("mutation", ("body", "mode"))
def test_runtime_seal_authenticates_console_wrapper_body_and_mode(
    tmp_path: Path,
    mutation: str,
) -> None:
    purelib, dist_info, expected = _tiny_installed_distribution(tmp_path)
    script = tmp_path / "venv/bin/demo-cli"
    script.parent.mkdir()
    executable = tmp_path / "venv/bin/python"
    executable.write_bytes(b"interpreter")
    valid_payload = _runtime_console_wrapper(executable, "demo.cli:main")
    script.write_bytes(valid_payload)
    script.chmod(0o755)
    rows = _record_rows((dist_info / "RECORD").read_bytes())
    rows.insert(
        -1,
        [
            "../../../bin/demo-cli",
            _record_digest(valid_payload),
            str(len(valid_payload)),
        ],
    )
    (dist_info / "RECORD").write_bytes(_render_record(rows))
    if mutation == "body":
        mutated_payload = valid_payload.replace(b"demo.cli", b"evil.cli")
        script.write_bytes(mutated_payload)
        rows[-2][1] = _record_digest(mutated_payload)
        rows[-2][2] = str(len(mutated_payload))
        (dist_info / "RECORD").write_bytes(_render_record(rows))
    else:
        script.chmod(0o700)
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_verify_installed_distribution"].__globals__

    with pytest.raises(namespace["RuntimeSealError"], match="wrapper|script"):
        module_globals["_verify_installed_distribution"](
            purelib,
            dist_info,
            "demo",
            "1.0",
            expected,
            None,
            {},
            {"demo-cli": "demo.cli:main"},
        )


def test_runtime_output_overlap_uses_physical_intermediate_components(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    package = source / "equipment_quality"
    physical_parent = package / "generated"
    wheelhouse = source / ".wheelhouse"
    runtime = tmp_path / "venv"
    alias_parent = tmp_path / "alias-parent"
    physical_parent.mkdir(parents=True)
    wheelhouse.mkdir()
    runtime.mkdir()
    alias_parent.mkdir()
    (alias_parent / "through").symlink_to(package, target_is_directory=True)
    producer_lock = source / "producer.lock"
    producer_lock.write_bytes(b"lock\n")
    output = alias_parent / "through/generated/runtime.json"
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_validate_topology"].__globals__

    with pytest.raises(namespace["RuntimeSealError"], match="symlink|physical|nested"):
        module_globals["_validate_topology"](
            source, wheelhouse, producer_lock, runtime, output
        )


def test_runtime_publication_never_unlinks_an_owned_or_replaceable_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_publish_manifest"].__globals__

    def forbidden_unlink(*_arguments: object, **_keywords: object) -> None:
        raise AssertionError("path unlink opens an identity-replacement race")

    monkeypatch.setattr(module_globals["os"], "unlink", forbidden_unlink)
    output = tmp_path / "runtime.json"
    payload = _canonical_json_bytes({"sealed": True})

    assert module_globals["_publish_manifest"](output, payload) == "created"

    assert output.read_bytes() == payload
    assert not list(tmp_path.glob(".sfep-runtime-*"))


def test_runtime_publication_detects_final_name_replacement_without_deleting_it(
    tmp_path: Path,
) -> None:
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_publish_manifest"].__globals__
    output = tmp_path / "runtime.json"
    external_payload = b"external replacement\n"

    def replace_published_name() -> None:
        output.unlink()
        output.write_bytes(external_payload)

    with pytest.raises(namespace["RuntimeSealError"], match="changed|replaced|identity"):
        module_globals["_publish_manifest"](
            output,
            _canonical_json_bytes({"sealed": True}),
            post_publish=replace_published_name,
        )

    assert output.read_bytes() == external_payload
    assert not list(tmp_path.glob(".sfep-runtime-*"))


def test_runtime_seal_rolls_back_new_manifest_on_final_joint_reauthentication(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    wheelhouse = tmp_path / "wheelhouse"
    runtime = tmp_path / "venv"
    for directory in (source, wheelhouse, runtime):
        directory.mkdir()
    producer_lock = source / "producer.lock"
    producer_lock.write_bytes(b"lock\n")
    output = tmp_path / "runtime.json"
    first = _canonical_json_bytes({"capture": 1})
    second = _canonical_json_bytes({"capture": 2})
    captures = 0
    namespace = _runtime_seal_namespace()
    module_globals = namespace["_run"].__globals__

    def changing_capture(*_arguments: object) -> bytes:
        nonlocal captures
        captures += 1
        return first if captures < 3 else second

    module_globals["_capture_manifest"] = changing_capture

    with pytest.raises(namespace["RuntimeSealError"], match="changed|reauth"):
        module_globals["_run"](
            source, wheelhouse, producer_lock, runtime, output
        )

    assert captures == 3
    assert output.is_file()
    assert output.read_bytes() == b""
    assert not list(tmp_path.glob(".sfep-runtime-*"))
