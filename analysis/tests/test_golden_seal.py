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
import re
import runpy
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import textwrap
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
PRODUCER_FILENAME = "sfep_equipment_quality-1.1.0-py3-none-any.whl"
EXPECTED_SOURCE_DIGEST = (
    "sha256:26eefd1d37546b1aeb1a56158bad06765cb40f21f975a1c2efe569410d5d9a05"
)
EXPECTED_PROVENANCE_DIGEST = (
    "sha256:93ba035df919f9dfbc2a69b510955e223023bf7ddd81689df6af7e31b393c58f"
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
    metadata_path = "sfep_equipment_quality-1.1.0.dist-info/METADATA"
    wheel_path = "sfep_equipment_quality-1.1.0.dist-info/WHEEL"
    record_path = "sfep_equipment_quality-1.1.0.dist-info/RECORD"
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
            return name, payload.replace(b"Version: 1.1.0", b"Version: 9.0.0")
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
            "sfep-equipment-quality==1.1.0 "
            f"--hash=sha256:{producer_digest}\n"
        ).encode("ascii")


def test_source_preimage_and_provenance_match_independent_literals() -> None:
    preimage = independent_source_preimage(ANALYSIS_ROOT)
    provenance = independently_expected_provenance(ANALYSIS_ROOT)

    assert len(source_tree_bytes(ANALYSIS_ROOT)) == 29
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
        "sfep-equipment-quality==1.1.0 "
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
    assert manifest["producer"]["version"] == "1.1.0"
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


# Task 9f: an independent, byte-for-byte seal for the reviewed golden Bundle.
_GOLDEN_CONTRACT_ROOT = ANALYSIS_ROOT.parent / "contracts/equipment-monitor/v1"
_GOLDEN_SEAL_TOOL = TOOLS_ROOT / "seal_golden_bundle.py"
_GOLDEN_OUTPUT_NAMES = (
    "analysis_config.json",
    "producer_runtime.json",
    "equipment_operating_ranges.json",
    "quality_risk_intervals.json",
    "replay_events.csv",
    "analysis_summary.json",
    "bundle_manifest.json",
    "expected_alerts.json",
)
_GOLDEN_ARTIFACT_ROLES = (
    ("analysis_config", "analysis_config.json", "sfep-analysis-config/v1"),
    ("producer_runtime", "producer_runtime.json", "sfep-producer-runtime/v1"),
    (
        "equipment_operating_ranges",
        "equipment_operating_ranges.json",
        "sfep-operating-ranges/v1",
    ),
    (
        "quality_risk_intervals",
        "quality_risk_intervals.json",
        "sfep-quality-rules/v1",
    ),
    ("replay_events", "replay_events.csv", "sfep-replay-events/v1"),
    ("analysis_summary", "analysis_summary.json", "sfep-analysis-summary/v1"),
)
_GOLDEN_EXPECTATION_LITERALS = {
    "analysis_summary.template.json": (
        383700,
        "20acdfe66fc1167340c58f2ff91a00621725bace0b3b65832ab8629e51e04f1d",
    ),
    "criteria_projection.jsonl": (
        79246,
        "6842fabd1cc79ca801e34bb0708498f4bea16317d4449e6d773e28e40c94ff5c",
    ),
    "equipment_operating_ranges.template.json": (
        106,
        "77fa2a0665d6d5bf0cbd754b56a86c38b159cfa26abee6685fc910cf7e546581",
    ),
    "expected_alerts.json": (
        1244,
        "2c3bb6bc8e29fe077e71fd8f1b54efaa272ca31ae2676e87be8bcf549bc550ed",
    ),
    "quality_risk_intervals.template.json": (
        260272,
        "d47e7d8c230f31f56719056fc3a79505847863a51e686b1aac1aee9505143996",
    ),
    "replay_events.template.csv": (
        47904,
        "c516b9f8cebc2642b9c9b75a3bc12f1608df651522ccb2a65c2eb5c1424ce17c",
    ),
}
_GOLDEN_SOURCE_LITERALS = {
    "sts_1sm_cc_1.csv": (
        1056,
        "c3bba5c7235166b6693ff657a25797933ea9d83c6392437af9b5141f4e56a68c",
    ),
    "sts_2fur_hr_2.csv": (
        1866,
        "973e864ed996ca48c44a00943efc0ea51a878b94b8aac7452c7254a572fe7e67",
    ),
    "sts_3ap_3.csv": (
        711,
        "efaef9f283a28e63dff54b26bc3dd42c6a72e20f0b75d2fa13a6ab47f41405c9",
    ),
}
_GOLDEN_SCHEMA_LITERALS = {
    "analysis_config.schema.json": (
        5073,
        "fad28561dfe9d9fe3cd09b025bb18c2101be053cb094b08442ea45963b86f549",
    ),
    "analysis_summary.schema.json": (
        40096,
        "6cb6077cc7093b90e464cc794604ca6b7682002a138ebed051801aee29f9d374",
    ),
    "bundle_manifest.schema.json": (
        5512,
        "666e880d296c0d7e3df5af1aa80e6865922ebfb337fb9aca48f93eddfd89e8a5",
    ),
    "equipment_operating_ranges.schema.json": (
        2545,
        "bee7d8be181dae4844c51d4627c5a1f068583b60a60c854f17035a8291cd7d89",
    ),
    "producer_runtime.schema.json": (
        2916,
        "97131d80a993d09d17c2c040b0e1cb2bd0eed5948d7a11608f26331d18f557e6",
    ),
    "quality_risk_intervals.schema.json": (
        5319,
        "2c8775fec18671030cf58ea0e94a3c99f8dac075fa9d5fe8dce724c2462f42ad",
    ),
    "replay_event_row.schema.json": (
        7865,
        "309749f73cf2a5a522617f975128ac005298f2fd71412903d76263702c1b6bd6",
    ),
}
_GOLDEN_ANALYSIS_CONFIG_LITERAL = (
    13830,
    "bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b",
)
_GOLDEN_RUNTIME_LITERAL = (
    5537,
    "3c1daabb3e868fc6cebc75023980fcb71d947996170de6d8e58cd06b18231407",
)
_GOLDEN_CRITERIA_ID = (
    "sha256:8c88d3109bd6945b76317ee415c7821524f515c9bd79d36b8072c285c6434af7"
)
_GOLDEN_BUNDLE_ID = (
    "sha256:4f40427690116f0defcbe609d9e5e1ef4025bb4193cdf099386659a26dd9f8bc"
)
_GOLDEN_OUTPUT_LITERALS = {
    "analysis_config.json": (
        13830,
        "bb2610971dc1b3adcb4e93b9d26a50fdb4070292ffc4bf4a26ce7cdde0f5fb0b",
    ),
    "analysis_summary.json": (
        383818,
        "aec581bca1e6b4eaed1ddae36c53cd3d5c70e2afb582589e1bc299dd2743876e",
    ),
    "bundle_manifest.json": (
        3676,
        "d717b150fcd7cf80065e2a71744091da387d38243f30d816f4cc83e63d605a33",
    ),
    "equipment_operating_ranges.json": (
        164,
        "bd8ab5dc79a1a7e97d64132dd3669befbe3fa28493a57eff5918818933526200",
    ),
    "expected_alerts.json": (
        2032,
        "bb1834b04cc847d52f1a5e2d9fa9268140dbbab4af86e69d310d1c1c1278e0ae",
    ),
    "producer_runtime.json": (
        5537,
        "3c1daabb3e868fc6cebc75023980fcb71d947996170de6d8e58cd06b18231407",
    ),
    "quality_risk_intervals.json": (
        260330,
        "3b0a85925ab1ae7b42d70989c32fad3ea3b881cf9647a6a13a72db3d42b7eb30",
    ),
    "replay_events.csv": (
        59114,
        "10b39aac41f317210074e42b44a0e1ab6f521b7c432647edf72be80a4e0fef8c",
    ),
}


def _golden_digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _golden_id(version: str, fields: dict[str, str]) -> str:
    lines = version + "\n" + "".join(
        f"{key}={fields[key]}\n"
        for key in sorted(fields, key=lambda item: item.encode("utf-8"))
    )
    return _golden_digest(lines.encode("utf-8"))


def _golden_independent_oracle(
    contract_root: Path = _GOLDEN_CONTRACT_ROOT,
    analysis_config: Path = ANALYSIS_ROOT / "analysis_config.json",
    runtime_manifest: Path = _GOLDEN_CONTRACT_ROOT / "golden-bundle/producer_runtime.json",
) -> tuple[str, str, dict[str, str], dict[str, str], dict[str, bytes]]:
    expectation = contract_root / "golden-expectation"
    source = contract_root / "golden-source"
    config_bytes = analysis_config.read_bytes()
    runtime_bytes = runtime_manifest.read_bytes()
    projection = (expectation / "criteria_projection.jsonl").read_bytes()
    schema_bytes = {
        name: (contract_root / name).read_bytes()
        for name in _GOLDEN_SCHEMA_LITERALS
    }
    schema_digests = {
        name: _golden_digest(payload) for name, payload in schema_bytes.items()
    }
    criteria_fields = {
        "analysis_config_sha256": _golden_digest(config_bytes),
        "as_of": "2025-02-20",
        "criteria_projection_sha256": _golden_digest(projection),
        "producer_runtime_sha256": _golden_digest(runtime_bytes),
        "schema.analysis_config.sha256": schema_digests[
            "analysis_config.schema.json"
        ],
        "schema.equipment_operating_ranges.sha256": schema_digests[
            "equipment_operating_ranges.schema.json"
        ],
        "schema.producer_runtime.sha256": schema_digests[
            "producer_runtime.schema.json"
        ],
        "schema.quality_risk_intervals.sha256": schema_digests[
            "quality_risk_intervals.schema.json"
        ],
    }
    criteria_id = _golden_id("sfep-criteria-id/v1", criteria_fields)
    source_specs = (
        ("sm_cc", "sts_1sm_cc_1.csv"),
        ("fur_hr", "sts_2fur_hr_2.csv"),
        ("ap", "sts_3ap_3.csv"),
    )
    bundle_fields = {
        "analysis_config_sha256": _golden_digest(config_bytes),
        "criteria_id": criteria_id,
        "producer_runtime_sha256": _golden_digest(runtime_bytes),
    }
    for role, name in source_specs:
        payload = (source / name).read_bytes()
        bundle_fields[f"source.{role}.name"] = name
        bundle_fields[f"source.{role}.sha256"] = _golden_digest(payload)
        bundle_fields[f"source.{role}.size_bytes"] = str(len(payload))
    for role, name in (
        ("analysis_config", "analysis_config.schema.json"),
        ("analysis_summary", "analysis_summary.schema.json"),
        ("bundle_manifest", "bundle_manifest.schema.json"),
        ("equipment_operating_ranges", "equipment_operating_ranges.schema.json"),
        ("producer_runtime", "producer_runtime.schema.json"),
        ("quality_risk_intervals", "quality_risk_intervals.schema.json"),
        ("replay_events", "replay_event_row.schema.json"),
    ):
        bundle_fields[f"schema.{role}.sha256"] = schema_digests[name]
    bundle_id = _golden_id("sfep-bundle-id/v1", bundle_fields)

    outputs = {
        "analysis_config.json": config_bytes,
        "producer_runtime.json": runtime_bytes,
        "equipment_operating_ranges.json": (
            expectation / "equipment_operating_ranges.template.json"
        ).read_bytes().replace(b"@CRITERIA_ID@", criteria_id.encode("ascii")),
        "quality_risk_intervals.json": (
            expectation / "quality_risk_intervals.template.json"
        ).read_bytes().replace(b"@CRITERIA_ID@", criteria_id.encode("ascii")),
        "replay_events.csv": (
            expectation / "replay_events.template.csv"
        ).read_bytes().replace(b"@BUNDLE_ID@", bundle_id.encode("ascii")).replace(
            b"@CRITERIA_ID@", criteria_id.encode("ascii")
        ),
        "analysis_summary.json": (
            expectation / "analysis_summary.template.json"
        ).read_bytes().replace(b"@BUNDLE_ID@", bundle_id.encode("ascii")).replace(
            b"@CRITERIA_ID@", criteria_id.encode("ascii")
        ),
    }
    artifact_metadata = [
        {
            "role": role,
            "schemaVersion": schema_version,
            "sha256": _golden_digest(outputs[name]),
            "sizeBytes": len(outputs[name]),
        }
        for role, name, schema_version in _GOLDEN_ARTIFACT_ROLES
    ]
    manifest = {
        "artifacts": artifact_metadata,
        "asOf": "2025-02-20",
        "bundleId": bundle_id,
        "criteriaId": criteria_id,
        "criteriaIdentity": criteria_fields,
        "identity": bundle_fields,
        "labelMaturityDays": 38,
        "schemaVersion": "sfep-equipment-bundle/v1",
        "timezone": "Asia/Seoul",
    }
    outputs["bundle_manifest.json"] = _canonical_json_bytes(manifest)
    token_values = {
        "ARTIFACT_ANALYSIS_CONFIG_SHA256": _golden_digest(
            outputs["analysis_config.json"]
        ),
        "ARTIFACT_ANALYSIS_SUMMARY_SHA256": _golden_digest(
            outputs["analysis_summary.json"]
        ),
        "ARTIFACT_EQUIPMENT_OPERATING_RANGES_SHA256": _golden_digest(
            outputs["equipment_operating_ranges.json"]
        ),
        "ARTIFACT_PRODUCER_RUNTIME_SHA256": _golden_digest(
            outputs["producer_runtime.json"]
        ),
        "ARTIFACT_QUALITY_RISK_INTERVALS_SHA256": _golden_digest(
            outputs["quality_risk_intervals.json"]
        ),
        "ARTIFACT_REPLAY_EVENTS_SHA256": _golden_digest(
            outputs["replay_events.csv"]
        ),
        "BUNDLE_ID": bundle_id,
        "CRITERIA_ID": criteria_id,
        "PRODUCER_RUNTIME_SHA256": _golden_digest(runtime_bytes),
        "SCHEMA_ANALYSIS_CONFIG_SHA256": schema_digests[
            "analysis_config.schema.json"
        ],
        "SCHEMA_ANALYSIS_SUMMARY_SHA256": schema_digests[
            "analysis_summary.schema.json"
        ],
        "SCHEMA_BUNDLE_MANIFEST_SHA256": schema_digests[
            "bundle_manifest.schema.json"
        ],
        "SCHEMA_EQUIPMENT_OPERATING_RANGES_SHA256": schema_digests[
            "equipment_operating_ranges.schema.json"
        ],
        "SCHEMA_PRODUCER_RUNTIME_SHA256": schema_digests[
            "producer_runtime.schema.json"
        ],
        "SCHEMA_QUALITY_RISK_INTERVALS_SHA256": schema_digests[
            "quality_risk_intervals.schema.json"
        ],
        "SCHEMA_REPLAY_EVENTS_SHA256": schema_digests[
            "replay_event_row.schema.json"
        ],
        "SOURCE_AP_SHA256": _golden_digest(
            (source / "sts_3ap_3.csv").read_bytes()
        ),
        "SOURCE_FUR_HR_SHA256": _golden_digest(
            (source / "sts_2fur_hr_2.csv").read_bytes()
        ),
        "SOURCE_SM_CC_SHA256": _golden_digest(
            (source / "sts_1sm_cc_1.csv").read_bytes()
        ),
    }
    alerts = (expectation / "expected_alerts.json").read_bytes()
    for token, value in token_values.items():
        alerts = alerts.replace(f"@{token}@".encode("ascii"), value.encode("ascii"))
    outputs["expected_alerts.json"] = alerts
    return criteria_id, bundle_id, criteria_fields, bundle_fields, outputs


def _copy_golden_seal_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "contract-v1"
    root.mkdir()
    shutil.copytree(
        _GOLDEN_CONTRACT_ROOT / "golden-expectation",
        root / "golden-expectation",
    )
    shutil.copytree(
        _GOLDEN_CONTRACT_ROOT / "golden-source",
        root / "golden-source",
    )
    for name in _GOLDEN_SCHEMA_LITERALS:
        shutil.copy2(_GOLDEN_CONTRACT_ROOT / name, root / name)
    config = tmp_path / "analysis_config.json"
    runtime = tmp_path / "producer_runtime.json"
    shutil.copy2(ANALYSIS_ROOT / "analysis_config.json", config)
    shutil.copy2(
        _GOLDEN_CONTRACT_ROOT / "golden-bundle/producer_runtime.json",
        runtime,
    )
    return root, config, runtime, tmp_path / "golden-bundle"


def _run_golden_seal(
    root: Path,
    config: Path,
    runtime: Path,
    output: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return _run_tool(
        "seal_golden_bundle.py",
        "--contract-root",
        str(root),
        "--analysis-config",
        str(config),
        "--runtime-manifest",
        str(runtime),
        "--output-dir",
        str(output),
        *extra,
    )


def test_golden_bundle_independent_oracle_freezes_ids_outputs_and_inputs() -> None:
    criteria_id, bundle_id, criteria_fields, bundle_fields, outputs = (
        _golden_independent_oracle()
    )

    assert criteria_id == _GOLDEN_CRITERIA_ID
    assert bundle_id == _GOLDEN_BUNDLE_ID
    assert len(criteria_fields) == 8
    assert len(bundle_fields) == 19
    assert {
        name: (len(payload), hashlib.sha256(payload).hexdigest())
        for name, payload in outputs.items()
    } == _GOLDEN_OUTPUT_LITERALS
    for root, literals in (
        (_GOLDEN_CONTRACT_ROOT / "golden-expectation", _GOLDEN_EXPECTATION_LITERALS),
        (_GOLDEN_CONTRACT_ROOT / "golden-source", _GOLDEN_SOURCE_LITERALS),
        (_GOLDEN_CONTRACT_ROOT, _GOLDEN_SCHEMA_LITERALS),
    ):
        assert {
            name: (len((root / name).read_bytes()), hashlib.sha256((root / name).read_bytes()).hexdigest())
            for name in literals
        } == literals
    assert (
        len((ANALYSIS_ROOT / "analysis_config.json").read_bytes()),
        hashlib.sha256((ANALYSIS_ROOT / "analysis_config.json").read_bytes()).hexdigest(),
    ) == _GOLDEN_ANALYSIS_CONFIG_LITERAL
    assert (
        len((_GOLDEN_CONTRACT_ROOT / "golden-bundle/producer_runtime.json").read_bytes()),
        hashlib.sha256(
            (_GOLDEN_CONTRACT_ROOT / "golden-bundle/producer_runtime.json").read_bytes()
        ).hexdigest(),
    ) == _GOLDEN_RUNTIME_LITERAL


def test_checked_in_golden_bundle_is_the_exact_independent_seal() -> None:
    output_root = _GOLDEN_CONTRACT_ROOT / "golden-bundle"
    _criteria_id, _bundle_id, _criteria_fields, _bundle_fields, expected = (
        _golden_independent_oracle()
    )

    assert {path.name for path in output_root.iterdir()} == set(_GOLDEN_OUTPUT_NAMES)
    assert {
        name: (output_root / name).read_bytes() for name in _GOLDEN_OUTPUT_NAMES
    } == expected
    assert {
        name: (
            len((output_root / name).read_bytes()),
            hashlib.sha256((output_root / name).read_bytes()).hexdigest(),
        )
        for name in _GOLDEN_OUTPUT_NAMES
    } == _GOLDEN_OUTPUT_LITERALS


def test_reviewed_templates_freeze_exact_token_names_and_occurrences() -> None:
    expectation = _GOLDEN_CONTRACT_ROOT / "golden-expectation"
    expected_counts = {
        "criteria_projection.jsonl": {},
        "equipment_operating_ranges.template.json": {"CRITERIA_ID": 1},
        "quality_risk_intervals.template.json": {"CRITERIA_ID": 1},
        "replay_events.template.csv": {"BUNDLE_ID": 95, "CRITERIA_ID": 95},
        "analysis_summary.template.json": {"BUNDLE_ID": 1, "CRITERIA_ID": 1},
        "expected_alerts.json": {
            name: 1
            for name in (
                "ARTIFACT_ANALYSIS_CONFIG_SHA256",
                "ARTIFACT_ANALYSIS_SUMMARY_SHA256",
                "ARTIFACT_EQUIPMENT_OPERATING_RANGES_SHA256",
                "ARTIFACT_PRODUCER_RUNTIME_SHA256",
                "ARTIFACT_QUALITY_RISK_INTERVALS_SHA256",
                "ARTIFACT_REPLAY_EVENTS_SHA256",
                "BUNDLE_ID",
                "CRITERIA_ID",
                "PRODUCER_RUNTIME_SHA256",
                "SCHEMA_ANALYSIS_CONFIG_SHA256",
                "SCHEMA_ANALYSIS_SUMMARY_SHA256",
                "SCHEMA_BUNDLE_MANIFEST_SHA256",
                "SCHEMA_EQUIPMENT_OPERATING_RANGES_SHA256",
                "SCHEMA_PRODUCER_RUNTIME_SHA256",
                "SCHEMA_QUALITY_RISK_INTERVALS_SHA256",
                "SCHEMA_REPLAY_EVENTS_SHA256",
                "SOURCE_AP_SHA256",
                "SOURCE_FUR_HR_SHA256",
                "SOURCE_SM_CC_SHA256",
            )
        },
    }

    for name, expected in expected_counts.items():
        tokens = [
            match.group(1).decode("ascii")
            for match in re.finditer(
                rb"@([A-Z0-9_]+)@", (expectation / name).read_bytes()
            )
        ]
        actual = {token: tokens.count(token) for token in set(tokens)}
        assert actual == expected


def test_golden_bundle_seal_cli_is_exact_stdlib_and_producer_independent() -> None:
    source = _GOLDEN_SEAL_TOOL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "equipment_quality" not in source
    assert imports <= set(sys.stdlib_module_names) | {"__future__"}
    assert "allow_abbrev=False" in source


def test_golden_bundle_seal_matches_independent_oracle_and_reuses(
    tmp_path: Path,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    _criteria_id, bundle_id, _criteria_fields, _bundle_fields, expected = (
        _golden_independent_oracle(root, config, runtime)
    )

    first = _run_golden_seal(root, config, runtime, output)
    second = _run_golden_seal(root, config, runtime, output)

    assert first.returncode == 0, first.stderr
    assert first.stdout == f"created {bundle_id}\n"
    assert second.returncode == 0, second.stderr
    assert second.stdout == f"reused {bundle_id}\n"
    assert second.stderr == ""
    assert tuple(sorted(path.name for path in output.iterdir())) == tuple(
        sorted(_GOLDEN_OUTPUT_NAMES)
    )
    assert {name: (output / name).read_bytes() for name in _GOLDEN_OUTPUT_NAMES} == expected
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE((output / name).stat().st_mode) == 0o600 for name in _GOLDEN_OUTPUT_NAMES)


def test_golden_bundle_seal_outputs_are_structural_and_reversible(
    tmp_path: Path,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    criteria_id, bundle_id, criteria_fields, bundle_fields, expected = (
        _golden_independent_oracle(root, config, runtime)
    )
    result = _run_golden_seal(root, config, runtime, output)
    assert result.returncode == 0, result.stderr

    for name in (
        "analysis_config.json",
        "producer_runtime.json",
        "equipment_operating_ranges.json",
        "quality_risk_intervals.json",
        "analysis_summary.json",
        "bundle_manifest.json",
        "expected_alerts.json",
    ):
        payload = expected[name]
        assert isinstance(json.loads(payload), dict)
        assert payload.startswith(b"{") and payload.endswith(b"}\n")
        assert b"\r" not in payload and b"\n" not in payload[:-1]
        assert not re.search(rb"@[A-Z0-9_]+@", payload)
    for name in (
        "producer_runtime.json",
        "equipment_operating_ranges.json",
        "bundle_manifest.json",
        "expected_alerts.json",
    ):
        assert expected[name] == _canonical_json_bytes(json.loads(expected[name]))
    replay = expected["replay_events.csv"].decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(replay, newline="")))
    assert len(rows) == 95
    assert {row["bundle_id"] for row in rows} == {bundle_id}
    assert {row["criteria_id"] for row in rows} == {criteria_id}
    alerts = json.loads(expected["expected_alerts.json"])
    assert alerts["alerts"] == []
    assert alerts["expectedReplayEventCount"] == 95
    manifest = json.loads(expected["bundle_manifest.json"])
    assert manifest["criteriaIdentity"] == criteria_fields
    assert manifest["identity"] == bundle_fields
    assert manifest["artifacts"] == [
        {
            "role": role,
            "schemaVersion": version,
            "sha256": _golden_digest(expected[name]),
            "sizeBytes": len(expected[name]),
        }
        for role, name, version in _GOLDEN_ARTIFACT_ROLES
    ]

    expectation = root / "golden-expectation"
    reverse_specs = {
        "equipment_operating_ranges.json": (
            "equipment_operating_ranges.template.json",
            ((criteria_id, "@CRITERIA_ID@"),),
        ),
        "quality_risk_intervals.json": (
            "quality_risk_intervals.template.json",
            ((criteria_id, "@CRITERIA_ID@"),),
        ),
        "replay_events.csv": (
            "replay_events.template.csv",
            ((bundle_id, "@BUNDLE_ID@"), (criteria_id, "@CRITERIA_ID@")),
        ),
        "analysis_summary.json": (
            "analysis_summary.template.json",
            ((bundle_id, "@BUNDLE_ID@"), (criteria_id, "@CRITERIA_ID@")),
        ),
    }
    for output_name, (template_name, substitutions) in reverse_specs.items():
        recovered = expected[output_name]
        for value, token in substitutions:
            recovered = recovered.replace(value.encode("ascii"), token.encode("ascii"))
        assert recovered == (expectation / template_name).read_bytes()


@pytest.mark.parametrize(
    ("group", "name"),
    [
        *(("expectation", name) for name in _GOLDEN_EXPECTATION_LITERALS),
        *(("source", name) for name in _GOLDEN_SOURCE_LITERALS),
        *(("schema", name) for name in _GOLDEN_SCHEMA_LITERALS),
        ("config", "analysis_config.json"),
        ("runtime", "producer_runtime.json"),
    ],
)
def test_golden_bundle_seal_rejects_every_pinned_input_drift(
    tmp_path: Path,
    group: str,
    name: str,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    targets = {
        "expectation": root / "golden-expectation" / name,
        "source": root / "golden-source" / name,
        "schema": root / name,
        "config": config,
        "runtime": runtime,
    }
    target = targets[group]
    target.write_bytes(target.read_bytes() + b"X")

    result = _run_golden_seal(root, config, runtime, output)

    assert result.returncode == 2
    assert result.stdout == ""
    assert re.search("size|sha256|pinned|authenticated", result.stderr, re.I)
    assert not output.exists()


@pytest.mark.parametrize("group", ["golden-expectation", "golden-source"])
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_golden_bundle_seal_rejects_semantic_inventory_drift(
    tmp_path: Path,
    group: str,
    change: str,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    directory = root / group
    if change == "missing":
        next(path for path in directory.iterdir() if path.is_file()).unlink()
    else:
        (directory / "unreviewed.txt").write_bytes(b"extra\n")

    result = _run_golden_seal(root, config, runtime, output)

    assert result.returncode == 2
    assert "inventory" in result.stderr.lower()
    assert not output.exists()


@pytest.mark.parametrize(
    ("template_name", "mutation"),
    [
        ("equipment_operating_ranges.template.json", "unknown"),
        ("equipment_operating_ranges.template.json", "missing"),
        ("equipment_operating_ranges.template.json", "duplicate"),
        ("replay_events.template.csv", "unknown"),
        ("expected_alerts.json", "missing"),
        ("expected_alerts.json", "duplicate"),
    ],
)
def test_golden_bundle_seal_rejects_token_boundary_mutations(
    tmp_path: Path,
    template_name: str,
    mutation: str,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    target = root / "golden-expectation" / template_name
    payload = target.read_bytes()
    tokens = re.findall(rb"@[A-Z0-9_]+@", payload)
    assert tokens
    if mutation == "unknown":
        payload = payload.replace(tokens[0], b"@UNREVIEWED_TOKEN@", 1)
    elif mutation == "missing":
        payload = payload.replace(tokens[0], b"", 1)
    else:
        payload = payload + tokens[0]
    target.write_bytes(payload)

    result = _run_golden_seal(root, config, runtime, output)

    assert result.returncode == 2
    assert not output.exists()


@pytest.mark.parametrize(
    "argument_index",
    [1, 3, 5, 7],
)
def test_golden_bundle_seal_rejects_relative_and_control_paths(
    tmp_path: Path,
    argument_index: int,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    arguments = [
        "--contract-root", str(root),
        "--analysis-config", str(config),
        "--runtime-manifest", str(runtime),
        "--output-dir", str(output),
    ]
    arguments[argument_index] = "relative\npath"
    result = _run_tool("seal_golden_bundle.py", *arguments, cwd=tmp_path)
    assert result.returncode == 2
    assert not output.exists()


@pytest.mark.parametrize(
    ("exact", "abbreviation"),
    [
        ("--contract-root", "--contract"),
        ("--analysis-config", "--analysis"),
        ("--runtime-manifest", "--runtime"),
        ("--output-dir", "--output"),
    ],
)
def test_golden_bundle_seal_rejects_flag_abbreviations(
    tmp_path: Path,
    exact: str,
    abbreviation: str,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    arguments = [
        "--contract-root", str(root),
        "--analysis-config", str(config),
        "--runtime-manifest", str(runtime),
        "--output-dir", str(output),
    ]
    arguments[arguments.index(exact)] = abbreviation
    result = _run_tool("seal_golden_bundle.py", *arguments)
    assert result.returncode == 2
    assert not output.exists()


def test_golden_bundle_seal_rejects_collision_without_modifying_it(
    tmp_path: Path,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    output.mkdir()
    sentinel = output / "external.txt"
    sentinel.write_bytes(b"external\n")

    result = _run_golden_seal(root, config, runtime, output)

    assert result.returncode == 2
    assert sentinel.read_bytes() == b"external\n"
    assert {path.name for path in output.iterdir()} == {"external.txt"}


def test_golden_bundle_seal_is_umask_independent(tmp_path: Path) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    old_umask = os.umask(0o777)
    try:
        result = _run_golden_seal(root, config, runtime, output)
    finally:
        os.umask(old_umask)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert {
        stat.S_IMODE((output / name).stat().st_mode) for name in _GOLDEN_OUTPUT_NAMES
    } == {0o600}


def _golden_seal_namespace() -> dict[str, object]:
    return runpy.run_path(str(_GOLDEN_SEAL_TOOL), run_name="golden_seal_test")


@pytest.mark.parametrize("alias_role", ["contract", "config", "runtime", "output-parent"])
def test_golden_bundle_seal_rejects_symlink_components(
    tmp_path: Path,
    alias_role: str,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    if alias_role == "contract":
        alias = tmp_path / "contract-alias"
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    elif alias_role == "config":
        alias = tmp_path / "config-alias.json"
        alias.symlink_to(config)
        config = alias
    elif alias_role == "runtime":
        alias = tmp_path / "runtime-alias.json"
        alias.symlink_to(runtime)
        runtime = alias
    else:
        physical = tmp_path / "physical-output-parent"
        physical.mkdir()
        alias = tmp_path / "output-parent-alias"
        alias.symlink_to(physical, target_is_directory=True)
        output = alias / "golden-bundle"

    result = _run_golden_seal(root, config, runtime, output)

    assert result.returncode == 2
    assert "symlink" in result.stderr.lower()
    assert not output.exists()


def test_golden_bundle_seal_rejects_special_input_without_blocking(
    tmp_path: Path,
) -> None:
    root, _config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    fifo = tmp_path / "analysis-config.fifo"
    os.mkfifo(fifo)
    command = [
        sys.executable,
        str(_GOLDEN_SEAL_TOOL),
        "--contract-root",
        str(root),
        "--analysis-config",
        str(fifo),
        "--runtime-manifest",
        str(runtime),
        "--output-dir",
        str(output),
    ]

    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )

    assert result.returncode == 2
    assert "regular" in result.stderr.lower()
    assert not output.exists()


def test_golden_bundle_seal_reauthenticates_all_inputs_at_commit_point(
    tmp_path: Path,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    namespace = _golden_seal_namespace()
    function_globals = namespace["_run"].__globals__
    original_capture = function_globals["_capture_inputs"]
    calls = 0

    def capture_then_mutate(*arguments: object) -> object:
        nonlocal calls
        calls += 1
        captured = original_capture(*arguments)
        if calls == 1:
            target = root / "golden-expectation/criteria_projection.jsonl"
            target.write_bytes(target.read_bytes() + b"changed\n")
        return captured

    function_globals["_capture_inputs"] = capture_then_mutate

    with pytest.raises(namespace["GoldenSealError"], match="pinned|changed|authenticated"):
        namespace["_run"](root, config, runtime, output)

    assert calls == 2
    assert not output.exists()
    assert list(tmp_path.glob(".sfep-golden-bundle-*"))


def test_golden_bundle_publication_fault_leaves_only_unclaimed_scratch(
    tmp_path: Path,
) -> None:
    namespace = _golden_seal_namespace()
    function_globals = namespace["_publish_bundle"].__globals__
    output = tmp_path / "golden-bundle"
    expected = _golden_independent_oracle()[-1]
    original_write = function_globals["_write_file"]
    calls = 0

    def fail_second_write(
        path: Path,
        payload: bytes,
        *arguments: object,
        **keywords: object,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise namespace["GoldenSealError"]("injected publication fault")
        original_write(path, payload, *arguments, **keywords)

    function_globals["_write_file"] = fail_second_write

    with pytest.raises(namespace["GoldenSealError"], match="injected"):
        namespace["_publish_bundle"](output, expected, lambda: None)

    assert not output.exists()
    scratch = list(tmp_path.glob(".sfep-golden-bundle-*"))
    assert len(scratch) == 1
    assert scratch[0].is_dir()


def test_golden_bundle_publication_race_never_deletes_competing_target(
    tmp_path: Path,
) -> None:
    namespace = _golden_seal_namespace()
    function_globals = namespace["_publish_bundle"].__globals__
    output = tmp_path / "golden-bundle"
    expected = _golden_independent_oracle()[-1]
    sentinel = b"external winner\n"

    def competing_install(*_arguments: object) -> None:
        output.mkdir()
        (output / "external.txt").write_bytes(sentinel)
        raise OSError(17, "File exists", str(output))

    function_globals["_atomic_install_exclusive"] = competing_install

    with pytest.raises(
        namespace["GoldenSealError"], match="inventory|differs|atomic|unclaimed"
    ):
        namespace["_publish_bundle"](output, expected, lambda: None)

    assert (output / "external.txt").read_bytes() == sentinel
    assert {path.name for path in output.iterdir()} == {"external.txt"}
    assert list(tmp_path.glob(".sfep-golden-bundle-*"))


def test_golden_bundle_publication_never_uses_pathname_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_seal_namespace()
    function_globals = namespace["_publish_bundle"].__globals__
    output = tmp_path / "golden-bundle"
    expected = _golden_independent_oracle()[-1]

    def forbidden_unlink(*_arguments: object, **_keywords: object) -> None:
        raise AssertionError("pathname deletion is forbidden")

    monkeypatch.setattr(function_globals["os"], "unlink", forbidden_unlink)

    assert namespace["_publish_bundle"](output, expected, lambda: None) == "created"
    assert {path.name for path in output.iterdir()} == set(_GOLDEN_OUTPUT_NAMES)


def test_golden_bundle_publication_rejects_scratch_inode_replacement_before_write(
    tmp_path: Path,
) -> None:
    namespace = _golden_seal_namespace()
    function_globals = namespace["_publish_bundle"].__globals__
    output = tmp_path / "golden-bundle"
    expected = _golden_independent_oracle()[-1]
    external = tmp_path / "external-directory"
    external.mkdir()
    external_inode = external.stat().st_ino
    original_write = function_globals["_write_file"]
    replaced_path: Path | None = None

    def replace_before_first_write(
        path: Path,
        payload: bytes,
        *arguments: object,
        **keywords: object,
    ) -> None:
        nonlocal replaced_path
        if replaced_path is None:
            scratch = path.parent
            displaced = tmp_path / "displaced-owned-scratch"
            scratch.rename(displaced)
            external.rename(scratch)
            replaced_path = scratch
        original_write(path, payload, *arguments, **keywords)

    function_globals["_write_file"] = replace_before_first_write

    with pytest.raises(namespace["GoldenSealError"], match="scratch|identity|replaced"):
        namespace["_publish_bundle"](output, expected, lambda: None)

    assert not output.exists()
    assert replaced_path is not None
    assert replaced_path.is_dir()
    assert replaced_path.stat().st_ino == external_inode


def test_golden_bundle_final_capture_jointly_rechecks_an_already_read_input(
    tmp_path: Path,
) -> None:
    root, config, runtime, output = _copy_golden_seal_inputs(tmp_path)
    namespace = _golden_seal_namespace()
    function_globals = namespace["_run"].__globals__
    original_capture = function_globals["_capture_inputs"]
    original_read = function_globals["_read_regular"]
    capture_calls = 0
    final_capture_active = False
    mutated = False

    def capture_with_phase(*arguments: object) -> object:
        nonlocal capture_calls, final_capture_active
        capture_calls += 1
        final_capture_active = capture_calls == 2
        try:
            return original_capture(*arguments)
        finally:
            final_capture_active = False

    def mutate_after_read(path: Path, label: str) -> object:
        nonlocal mutated
        captured = original_read(path, label)
        if final_capture_active and label == "analysis config" and not mutated:
            config.write_bytes(config.read_bytes() + b"changed-after-read\n")
            mutated = True
        return captured

    function_globals["_capture_inputs"] = capture_with_phase
    function_globals["_read_regular"] = mutate_after_read

    with pytest.raises(namespace["GoldenSealError"], match="pinned|changed|joint|authenticated"):
        namespace["_run"](root, config, runtime, output)

    assert mutated
    assert capture_calls == 2
    assert not output.exists()


# Task 6: seal the authenticated v1 semantics under the v2 seed contract.
_GOLDEN_V2_SEAL_TOOL = TOOLS_ROOT / "seal_golden_bundle_v2.py"
_GOLDEN_V2_CONFIG = (
    ANALYSIS_ROOT.parent
    / "contracts/equipment-monitor/v2/golden-config/analysis_config.json"
)
_GOLDEN_V2_BUNDLE = (
    ANALYSIS_ROOT.parent / "contracts/equipment-monitor/v2/golden-bundle"
)
_GOLDEN_V2_OUTPUT_NAMES = (
    "analysis_config.json",
    "producer_runtime.json",
    "equipment_operating_ranges.json",
    "quality_risk_intervals.json",
    "replay_events.csv",
    "analysis_summary.json",
    "bundle_manifest.json",
)


def _compat_runtime_python() -> Path:
    runtime = os.environ.get("SFEP_COMPAT_RUNTIME")
    if runtime is None:
        pytest.skip("SFEP_COMPAT_RUNTIME is required for compatibility reseal tests")
    python = Path(runtime) / "bin/python"
    if not python.is_file():
        pytest.skip("SFEP_COMPAT_RUNTIME/bin/python is unavailable")
    return python


def _run_golden_v2_seal(
    contract_root: Path,
    analysis_config: Path,
    runtime_manifest: Path,
    output: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(_compat_runtime_python()),
            "-S",
            str(_GOLDEN_V2_SEAL_TOOL),
            "--contract-root",
            str(contract_root),
            "--analysis-config",
            str(analysis_config),
            "--runtime-manifest",
            str(runtime_manifest),
            "--output-dir",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONHASHSEED": "0",
            "TZ": "Asia/Seoul",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


def test_golden_v2_cli_rejects_startup_without_no_site_mode() -> None:
    result = subprocess.run(
        [str(_compat_runtime_python()), str(_GOLDEN_V2_SEAL_TOOL), "--help"],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "Asia/Seoul",
        },
    )
    assert result.returncode == 2
    assert "requires Python -S" in result.stderr


def test_golden_v2_no_site_startup_never_executes_runtime_pth(
    tmp_path: Path,
) -> None:
    runtime_python = _compat_runtime_python()
    purelib = runtime_python.parents[1] / "lib/python3.12/site-packages"
    attack_pth = purelib / "zz_sfep_startup_attack.pth"
    marker = tmp_path / "pth-executed"
    attack_pth.write_text(
        "import pathlib; "
        f"pathlib.Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    environment = {
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TZ": "Asia/Seoul",
    }
    try:
        old_startup = subprocess.run(
            [str(runtime_python), "-c", "pass"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert old_startup.returncode == 0, old_startup.stderr
        assert marker.read_text(encoding="utf-8") == "executed"
        marker.unlink()
        result = _run_golden_v2_seal(
            _GOLDEN_CONTRACT_ROOT,
            _GOLDEN_V2_CONFIG,
            _GOLDEN_V2_BUNDLE / "producer_runtime.json",
            tmp_path / "output",
        )
    finally:
        attack_pth.unlink(missing_ok=True)

    assert result.returncode == 2
    assert "unclaimed" in result.stderr or "inventory" in result.stderr
    assert not marker.exists()
    assert not (tmp_path / "output").exists()


def _tree_file_hashes(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            len(path.read_bytes()),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().encode())
        if path.is_file()
    }


def test_golden_v2_seal_cli_is_exact_and_imports_only_the_standard_library() -> None:
    source = _GOLDEN_V2_SEAL_TOOL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert imports <= set(sys.stdlib_module_names) | {"__future__"}

    result = subprocess.run(
        [sys.executable, "-S", str(_GOLDEN_V2_SEAL_TOOL), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--contract-root" in result.stdout
    assert "--analysis-config" in result.stdout
    assert "--runtime-manifest" in result.stdout
    assert "--output-dir" in result.stdout
    assert "--output " not in result.stdout


def test_golden_v2_seal_is_byte_deterministic_and_v1_read_only(
    tmp_path: Path,
) -> None:
    before = _tree_file_hashes(_GOLDEN_CONTRACT_ROOT)
    runtime = _GOLDEN_V2_BUNDLE / "producer_runtime.json"
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_result = _run_golden_v2_seal(
        _GOLDEN_CONTRACT_ROOT, _GOLDEN_V2_CONFIG, runtime, first
    )
    second_result = _run_golden_v2_seal(
        _GOLDEN_CONTRACT_ROOT, _GOLDEN_V2_CONFIG, runtime, second
    )

    assert first_result.returncode == 0, first_result.stderr
    assert second_result.returncode == 0, second_result.stderr
    assert {path.name for path in first.iterdir()} == set(_GOLDEN_V2_OUTPUT_NAMES)
    assert {
        name: (first / name).read_bytes() for name in _GOLDEN_V2_OUTPUT_NAMES
    } == {
        name: (second / name).read_bytes() for name in _GOLDEN_V2_OUTPUT_NAMES
    } == {
        name: (_GOLDEN_V2_BUNDLE / name).read_bytes()
        for name in _GOLDEN_V2_OUTPUT_NAMES
    }
    assert _tree_file_hashes(_GOLDEN_CONTRACT_ROOT) == before


@pytest.mark.parametrize("tamper", ("v1-manifest", "v1-artifact", "v2-seed"))
def test_golden_v2_seal_rejects_unauthenticated_v1_or_wrong_seed_before_output(
    tmp_path: Path,
    tamper: str,
) -> None:
    contract = tmp_path / "contract-v1"
    shutil.copytree(_GOLDEN_CONTRACT_ROOT, contract)
    config = tmp_path / "analysis_config.json"
    shutil.copy2(_GOLDEN_V2_CONFIG, config)
    if tamper == "v1-manifest":
        path = contract / "golden-bundle/bundle_manifest.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif tamper == "v1-artifact":
        path = contract / "golden-bundle/replay_events.csv"
        path.write_bytes(path.read_bytes() + b" ")
    else:
        value = json.loads(config.read_bytes())
        value["bootstrap"]["seedMaterial"] = "sha256:" + "0" * 64
        config.write_bytes(_canonical_json_bytes(value))
    output = tmp_path / "output"

    result = _run_golden_v2_seal(
        contract,
        config,
        _GOLDEN_V2_BUNDLE / "producer_runtime.json",
        output,
    )

    assert result.returncode == 2
    assert not output.exists()


def test_golden_v2_seal_is_strict_no_clobber(tmp_path: Path) -> None:
    output = tmp_path / "occupied"
    output.mkdir()
    sentinel = output / "external.txt"
    sentinel.write_bytes(b"external\n")

    result = _run_golden_v2_seal(
        _GOLDEN_CONTRACT_ROOT,
        _GOLDEN_V2_CONFIG,
        _GOLDEN_V2_BUNDLE / "producer_runtime.json",
        output,
    )

    assert result.returncode == 2
    assert sentinel.read_bytes() == b"external\n"
    assert {path.name for path in output.iterdir()} == {"external.txt"}


def test_golden_v2_publication_fault_never_exposes_partial_target(
    tmp_path: Path,
) -> None:
    namespace = runpy.run_path(str(_GOLDEN_V2_SEAL_TOOL), run_name="v2_seal_test")
    module_globals = namespace["_publish_bundle"].__globals__
    original = module_globals["_write_file"]
    calls = 0

    def fail_second(path: Path, payload: bytes, *args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise namespace["GoldenV2SealError"]("injected publication fault")
        original(path, payload, *args, **kwargs)

    module_globals["_write_file"] = fail_second
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}

    with pytest.raises(namespace["GoldenV2SealError"], match="injected"):
        module_globals["_publish_bundle"](output, payloads, lambda: None)

    assert not output.exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []


def _golden_v2_seal_namespace() -> dict[str, object]:
    return runpy.run_path(str(_GOLDEN_V2_SEAL_TOOL), run_name="v2_seal_test")


def _golden_v2_inputs(namespace: dict[str, object]) -> dict[str, bytes]:
    return namespace["_capture_inputs"](
        _GOLDEN_CONTRACT_ROOT,
        _GOLDEN_V2_CONFIG,
        _GOLDEN_V2_BUNDLE / "producer_runtime.json",
    )[0]


def _v2_identity_uri(namespace: dict[str, object], name: str, fields: dict[str, str]) -> str:
    expected = set(fields)
    return namespace["_identity"](name, fields, expected)


def _coherently_forge_generated_v2(
    namespace: dict[str, object],
    destination: Path,
    *,
    criteria_change: tuple[str, str] | None = None,
    identity_change: tuple[str, str] | None = None,
) -> Path:
    shutil.copytree(_GOLDEN_V2_BUNDLE, destination)
    manifest_path = destination / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if criteria_change is not None:
        key, value = criteria_change
        manifest["criteriaIdentity"][key] = value
        if key == "as_of":
            manifest["asOf"] = value
        criteria_id = _v2_identity_uri(
            namespace, "sfep-criteria-id/v1", manifest["criteriaIdentity"]
        )
        manifest["criteriaId"] = criteria_id
        manifest["identity"]["criteria_id"] = criteria_id
        for filename in ("equipment_operating_ranges.json", "quality_risk_intervals.json"):
            value_object = json.loads((destination / filename).read_bytes())
            value_object["criteriaId"] = criteria_id
            if key == "as_of":
                value_object["asOf"] = value
            (destination / filename).write_bytes(_canonical_json_bytes(value_object))
        summary = json.loads((destination / "analysis_summary.json").read_bytes())
        summary["criteriaId"] = criteria_id
        if key == "as_of":
            summary["asOf"] = value
        (destination / "analysis_summary.json").write_bytes(_canonical_json_bytes(summary))
    if identity_change is not None:
        key, value = identity_change
        manifest["identity"][key] = value
    bundle_id = _v2_identity_uri(
        namespace, "sfep-bundle-id/v1", manifest["identity"]
    )
    manifest["bundleId"] = bundle_id
    summary_path = destination / "analysis_summary.json"
    summary = json.loads(summary_path.read_bytes())
    summary["bundleId"] = bundle_id
    summary_path.write_bytes(_canonical_json_bytes(summary))
    replay_path = destination / "replay_events.csv"
    rows = list(csv.reader(io.StringIO(replay_path.read_text(encoding="utf-8"), newline="")))
    for row in rows[1:]:
        row[rows[0].index("bundle_id")] = bundle_id
        row[rows[0].index("criteria_id")] = manifest["criteriaId"]
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    replay_path.write_bytes(output.getvalue().encode())
    filename_by_role = {
        role: filename for role, filename, _version in namespace["_ARTIFACTS"]
    }
    for entry in manifest["artifacts"]:
        payload = (destination / filename_by_role[entry["role"]]).read_bytes()
        entry["sizeBytes"] = len(payload)
        entry["sha256"] = _sha256_uri(payload)
    manifest_path.write_bytes(_canonical_json_bytes(manifest))
    rebound = destination.with_name(bundle_id)
    destination.rename(rebound)
    return rebound


@pytest.mark.parametrize(
    ("kind", "field"),
    (
        ("criteria", "criteria_projection_sha256"),
        ("criteria", "as_of"),
        ("identity", "source.ap.sha256"),
    ),
)
def test_golden_v2_verifier_rejects_coherent_identity_forgery(
    tmp_path: Path,
    kind: str,
    field: str,
) -> None:
    namespace = _golden_v2_seal_namespace()
    value = "2025-02-19" if field == "as_of" else "sha256:" + "9" * 64
    forged = _coherently_forge_generated_v2(
        namespace,
        tmp_path / "working",
        criteria_change=(field, value) if kind == "criteria" else None,
        identity_change=(field, value) if kind == "identity" else None,
    )

    with pytest.raises(namespace["GoldenV2SealError"]):
        namespace["_verify_generated"](forged, _golden_v2_inputs(namespace))


def test_golden_v2_verifier_rejects_schema_invalid_rebound_manifest(
    tmp_path: Path,
) -> None:
    namespace = _golden_v2_seal_namespace()
    bundle_id = json.loads(
        (_GOLDEN_V2_BUNDLE / "bundle_manifest.json").read_bytes()
    )["bundleId"]
    bundle = tmp_path / bundle_id
    shutil.copytree(_GOLDEN_V2_BUNDLE, bundle)
    manifest_path = bundle / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["forgedAdditionalMember"] = True
    manifest_path.write_bytes(_canonical_json_bytes(manifest))

    with pytest.raises(namespace["GoldenV2SealError"], match="schema|manifest"):
        namespace["_verify_generated"](bundle, _golden_v2_inputs(namespace))


def test_golden_v2_schema_verifier_rejects_bool_substituted_for_integer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_verify_generated"].__globals__
    manifest = json.loads(
        (_GOLDEN_V2_BUNDLE / "bundle_manifest.json").read_bytes()
    )
    bundle = tmp_path / manifest["bundleId"]
    shutil.copytree(_GOLDEN_V2_BUNDLE, bundle)
    summary_path = bundle / "analysis_summary.json"
    summary = json.loads(summary_path.read_bytes())
    assert summary["splitCounts"]["discovery"]["defects"] == 1
    summary["splitCounts"]["discovery"]["defects"] = True
    summary_payload = namespace["_canonical_bytes"](summary)
    summary_path.write_bytes(summary_payload)
    summary_entry = next(
        entry
        for entry in manifest["artifacts"]
        if entry["role"] == "analysis_summary"
    )
    summary_entry["sha256"] = _sha256_uri(summary_payload)
    summary_entry["sizeBytes"] = len(summary_payload)
    (bundle / "bundle_manifest.json").write_bytes(_canonical_json_bytes(manifest))
    original = globals_["_validate_output_schemas"]
    calls = 0

    def recording_schema_validation(outputs: object) -> None:
        nonlocal calls
        calls += 1
        original(outputs)

    globals_["_validate_output_schemas"] = recording_schema_validation
    monkeypatch.setattr(globals_["sys"], "executable", str(_compat_runtime_python()))
    with pytest.raises(
        namespace["GoldenV2SealError"],
        match="generated v2 schema validation failed",
    ) as captured:
        namespace["_verify_generated"](bundle, _golden_v2_inputs(namespace))
    assert calls == 1
    assert "True is not of type 'integer'" in str(captured.value)


@pytest.mark.parametrize(
    ("surface", "field"),
    (
        ("equipment_operating_ranges.json", "criteriaId"),
        ("quality_risk_intervals.json", "criteriaId"),
        ("replay_events.csv", "criteria_id"),
        ("replay_events.csv", "bundle_id"),
        ("analysis_summary.json", "criteriaId"),
        ("analysis_summary.json", "bundleId"),
    ),
)
def test_golden_v2_verifier_rejects_every_unbound_generated_identity_surface(
    tmp_path: Path,
    surface: str,
    field: str,
) -> None:
    namespace = _golden_v2_seal_namespace()
    manifest = json.loads(
        (_GOLDEN_V2_BUNDLE / "bundle_manifest.json").read_bytes()
    )
    bundle = tmp_path / manifest["bundleId"]
    shutil.copytree(_GOLDEN_V2_BUNDLE, bundle)
    if surface.endswith(".csv"):
        path = bundle / surface
        rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
        rows[1][rows[0].index(field)] = "sha256:" + "9" * 64
        output = io.StringIO(newline="")
        csv.writer(output, lineterminator="\n").writerows(rows)
        payload = output.getvalue().encode()
    else:
        path = bundle / surface
        value = json.loads(path.read_bytes())
        value[field] = "sha256:" + "9" * 64
        payload = namespace["_canonical_bytes"](value)
    path.write_bytes(payload)
    role = next(
        role
        for role, filename, _version in namespace["_ARTIFACTS"]
        if filename == surface
    )
    entry = next(item for item in manifest["artifacts"] if item["role"] == role)
    entry["sizeBytes"] = len(payload)
    entry["sha256"] = _sha256_uri(payload)
    (bundle / "bundle_manifest.json").write_bytes(namespace["_canonical_bytes"](manifest))

    with pytest.raises(namespace["GoldenV2SealError"], match="binding|parity"):
        namespace["_verify_generated"](bundle, _golden_v2_inputs(namespace))


@pytest.mark.parametrize(
    "payload",
    (
        b'{"value":0.005}\n',
        b'{"b":1,"a":2}\n',
        b'{"a": 1}\n',
    ),
)
def test_golden_v2_json_parser_rejects_noncanonical_json(payload: bytes) -> None:
    namespace = _golden_v2_seal_namespace()
    with pytest.raises(namespace["GoldenV2SealError"], match="canonical"):
        namespace["_json"](payload, "attack JSON")


def test_golden_v2_json_parser_rejects_duplicate_members() -> None:
    namespace = _golden_v2_seal_namespace()
    with pytest.raises(namespace["GoldenV2SealError"], match="duplicate"):
        namespace["_json"](b'{"a":1,"a":1}\n', "attack JSON")


def test_golden_v2_canonical_numbers_match_every_normative_vector() -> None:
    namespace = _golden_v2_seal_namespace()
    vectors = json.loads(
        (
            ANALYSIS_ROOT.parent
            / "contracts/equipment-monitor/v1/canonical-number-test-vectors.json"
        ).read_bytes()
    )
    assert tuple(
        namespace["_canonical_float"](float.fromhex(vector["hex"]))
        for vector in vectors
    ) == tuple(vector["expected"] for vector in vectors)


@pytest.mark.parametrize("tamper", ("crlf", "values-json"))
def test_golden_v2_event_parser_rejects_noncanonical_csv(
    tamper: str,
) -> None:
    namespace = _golden_v2_seal_namespace()
    payload = (_GOLDEN_V2_BUNDLE / "replay_events.csv").read_bytes()
    if tamper == "crlf":
        payload = payload.replace(b"\n", b"\r\n")
    else:
        payload = payload.replace(b"5e-3", b"0.005", 1)
    with pytest.raises(namespace["GoldenV2SealError"], match="canonical"):
        namespace["_events"](payload)


def test_golden_v2_reader_and_inventory_reject_hardlinks(tmp_path: Path) -> None:
    namespace = _golden_v2_seal_namespace()
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    alias = tmp_path / "alias"
    os.link(source, alias)
    with pytest.raises(namespace["GoldenV2SealError"], match="hardlink|link"):
        namespace["_read_file"](source, "hardlinked input")
    with pytest.raises(namespace["GoldenV2SealError"], match="hardlink|link"):
        namespace["_inventory"](tmp_path, {"source", "alias"}, "hardlinked inventory")


def test_golden_v2_reader_rejects_in_place_mode_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_read_file"].__globals__
    target = tmp_path / "target"
    target.write_bytes(b"payload")
    original = globals_["os"].read
    changed = False

    def chmod_during_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        payload = original(descriptor, size)
        if payload and not changed:
            os.fchmod(descriptor, 0o400)
            changed = True
        return payload

    monkeypatch.setattr(globals_["os"], "read", chmod_during_read)
    with pytest.raises(namespace["GoldenV2SealError"], match="changed"):
        namespace["_read_file"](target, "raced input")
    assert changed


def test_golden_v2_publication_rejects_hardlinked_scratch_and_cleans_it(
    tmp_path: Path,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    original = globals_["_write_file"]
    outside = tmp_path / "outside-hardlink"
    linked = False

    def hardlink_first(path: Path, payload: bytes, *args: object, **kwargs: object) -> None:
        nonlocal linked
        original(path, payload, *args, **kwargs)
        if not linked:
            os.link(path, outside)
            linked = True

    globals_["_write_file"] = hardlink_first
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(namespace["GoldenV2SealError"], match="hardlink|link"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert outside.is_file()
    assert not output.exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []


def test_golden_v2_publication_rejects_parent_swap_and_cleans_owned_scratch(
    tmp_path: Path,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    parent = tmp_path / "parent"
    parent.mkdir()
    output = parent / "output"
    displaced = tmp_path / "displaced-parent"
    original = globals_["_write_file"]
    swapped = False

    def swap_parent(path: Path, payload: bytes, *args: object, **kwargs: object) -> None:
        nonlocal swapped
        original(path, payload, *args, **kwargs)
        if not swapped:
            parent.rename(displaced)
            parent.mkdir()
            swapped = True

    globals_["_write_file"] = swap_parent
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(namespace["GoldenV2SealError"], match="parent|identity|changed"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert swapped
    assert not output.exists()
    assert list(parent.glob(".sfep-golden-v2-*")) == []
    assert list(displaced.glob(".sfep-golden-v2-*")) == []


def test_golden_v2_publication_rejects_ancestor_swap_and_cleans_owned_scratch(
    tmp_path: Path,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    ancestor = tmp_path / "ancestor"
    parent = ancestor / "parent"
    parent.mkdir(parents=True)
    output = parent / "output"
    displaced = tmp_path / "displaced-ancestor"
    original = globals_["_write_file"]
    swapped = False

    def swap_ancestor(path: Path, payload: bytes, *args: object, **kwargs: object) -> None:
        nonlocal swapped
        original(path, payload, *args, **kwargs)
        if not swapped:
            ancestor.rename(displaced)
            parent.mkdir(parents=True)
            swapped = True

    globals_["_write_file"] = swap_ancestor
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(namespace["GoldenV2SealError"], match="parent|identity|changed"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert swapped
    assert not output.exists()
    assert list(parent.glob(".sfep-golden-v2-*")) == []
    assert list((displaced / "parent").glob(".sfep-golden-v2-*")) == []


def test_golden_v2_publication_rejects_symlink_replacement_and_cleans_owned_scratch(
    tmp_path: Path,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    original = globals_["_write_file"]
    outside = tmp_path / "outside"
    outside.write_bytes(b"external")
    replaced = False

    def replace_first(path: Path, payload: bytes, *args: object, **kwargs: object) -> None:
        nonlocal replaced
        original(path, payload, *args, **kwargs)
        if not replaced:
            path.unlink()
            path.symlink_to(outside)
            replaced = True

    globals_["_write_file"] = replace_first
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(namespace["GoldenV2SealError"], match="regular|replaced|identity"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert outside.read_bytes() == b"external"
    assert not output.exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []


def test_golden_v2_publication_cleans_only_identity_pinned_replaced_scratch(
    tmp_path: Path,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    original = globals_["_write_file"]
    external = tmp_path / "external"
    external.mkdir()
    external_inode = external.stat().st_ino
    displaced = tmp_path / "owned-displaced"
    replacement: Path | None = None

    def replace_scratch(path: Path, payload: bytes, *args: object, **kwargs: object) -> None:
        nonlocal replacement
        if replacement is None:
            path.parent.rename(displaced)
            external.rename(path.parent)
            replacement = path.parent
        original(path, payload, *args, **kwargs)

    globals_["_write_file"] = replace_scratch
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(namespace["GoldenV2SealError"], match="scratch|identity"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert replacement is not None
    assert replacement.is_dir() and replacement.stat().st_ino == external_inode
    assert not displaced.exists()
    assert not output.exists()


def test_golden_v2_publication_rejects_unowned_lock_without_removing_it(
    tmp_path: Path,
) -> None:
    namespace = _golden_v2_seal_namespace()
    lock = tmp_path / ".sfep-golden-v2.lock"
    lock.write_bytes(b"unowned")
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(namespace["GoldenV2SealError"], match="lock"):
        namespace["_publish_bundle"](output, payloads, lambda: None)
    assert lock.read_bytes() == b"unowned"
    assert not output.exists()


def test_golden_v2_publication_rejects_lock_replacement_without_deleting_it(
    tmp_path: Path,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    original = globals_["_write_file"]
    replacement = b"unowned replacement"
    replaced = False

    def replace_lock(path: Path, payload: bytes, *args: object, **kwargs: object) -> None:
        nonlocal replaced
        original(path, payload, *args, **kwargs)
        if not replaced:
            lock = path.parent.parent / globals_["_PUBLICATION_LOCK"]
            lock.unlink()
            lock.write_bytes(replacement)
            replaced = True

    globals_["_write_file"] = replace_lock
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(namespace["GoldenV2SealError"], match="lock"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert replaced
    assert (tmp_path / globals_["_PUBLICATION_LOCK"]).read_bytes() == replacement
    assert not output.exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []


def test_golden_v2_publication_cleans_lock_if_initial_fchmod_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_fchmod = os.fchmod
    failed = False

    def failing_first_fchmod(descriptor: int, mode: int) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected lock fchmod failure")
        real_fchmod(descriptor, mode)

    monkeypatch.setattr(globals_["os"], "fchmod", failing_first_fchmod)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(OSError, match="injected lock fchmod failure"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert failed
    assert not output.exists()
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []


def test_golden_v2_publication_cleans_lock_if_initial_fstat_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_open = os.open
    real_fstat = os.fstat
    lock_descriptor: int | None = None
    failed = False

    def recording_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal lock_descriptor
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == globals_["_PUBLICATION_LOCK"]:
            lock_descriptor = descriptor
        return descriptor

    def failing_first_lock_fstat(descriptor: int) -> os.stat_result:
        nonlocal failed
        if descriptor == lock_descriptor and not failed:
            failed = True
            raise OSError("injected initial lock fstat failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(globals_["os"], "open", recording_open)
    monkeypatch.setattr(globals_["os"], "fstat", failing_first_lock_fstat)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(OSError, match="injected initial lock fstat failure"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert failed and lock_descriptor is not None
    assert not output.exists()
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []


@pytest.mark.parametrize("fault", ("scratch-fstat",))
def test_golden_v2_publication_cleans_early_owned_scratch_on_descriptor_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_open = os.open
    real_fstat = os.fstat
    opened: list[int] = []
    scratch_descriptor: int | None = None
    failed = False

    def faulting_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal failed, scratch_descriptor
        if (
            fault == "scratch-open"
            and isinstance(path, str)
            and path.startswith(globals_["_SCRATCH_PREFIX"])
            and not failed
        ):
            failed = True
            raise OSError("injected scratch open failure")
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        opened.append(descriptor)
        if isinstance(path, str) and path.startswith(globals_["_SCRATCH_PREFIX"]):
            scratch_descriptor = descriptor
        return descriptor

    def faulting_fstat(descriptor: int) -> os.stat_result:
        nonlocal failed
        if fault == "scratch-fstat" and descriptor == scratch_descriptor and not failed:
            failed = True
            raise OSError("injected scratch fstat failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(globals_["os"], "open", faulting_open)
    monkeypatch.setattr(globals_["os"], "fstat", faulting_fstat)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(OSError, match="injected scratch"):
        globals_["_publish_bundle"](output, payloads, lambda: None)

    assert failed
    assert not output.exists()
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []
    for descriptor in set(opened):
        with pytest.raises(OSError):
            real_fstat(descriptor)


@pytest.mark.parametrize("fault", ("identity-stat", "scratch-open"))
def test_golden_v2_publication_retries_one_shot_scratch_acquisition_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_open = os.open
    real_stat = os.stat
    real_fstat = os.fstat
    opened: list[int] = []
    failed = False

    def faulting_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal failed
        if (
            fault == "scratch-open"
            and isinstance(path, str)
            and path.startswith(globals_["_SCRATCH_PREFIX"])
            and not failed
        ):
            failed = True
            raise OSError("injected one-shot scratch open failure")
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        opened.append(descriptor)
        return descriptor

    def faulting_stat(
        path: object,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal failed
        if (
            fault == "identity-stat"
            and isinstance(path, str)
            and path.startswith(globals_["_SCRATCH_PREFIX"])
            and dir_fd is not None
            and not follow_symlinks
            and not failed
        ):
            failed = True
            raise OSError("injected one-shot scratch identity stat failure")
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(globals_["os"], "open", faulting_open)
    monkeypatch.setattr(globals_["os"], "stat", faulting_stat)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}

    status = globals_["_publish_bundle"](output, payloads, lambda: None)

    assert failed and status == "created"
    assert {path.name for path in output.iterdir()} == set(_GOLDEN_V2_OUTPUT_NAMES)
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []
    for descriptor in set(opened):
        with pytest.raises(OSError):
            real_fstat(descriptor)


def test_golden_v2_publication_preserves_ambiguous_mkdir_identity_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_stat = os.stat
    displaced = tmp_path / "ambiguous-owned-scratch"
    replacement: Path | None = None

    def replace_before_first_identity(
        path: object,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replacement
        if (
            replacement is None
            and isinstance(path, str)
            and path.startswith(globals_["_SCRATCH_PREFIX"])
            and dir_fd is not None
            and not follow_symlinks
        ):
            owned = tmp_path / path
            owned.rename(displaced)
            owned.mkdir()
            replacement = owned
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(globals_["os"], "stat", replace_before_first_identity)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}

    with pytest.raises(namespace["GoldenV2SealError"], match="ambiguous|inventory"):
        globals_["_publish_bundle"](output, payloads, lambda: None)

    assert replacement is not None and replacement.is_dir()
    assert displaced.is_dir()
    assert not output.exists()
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert [path for path in tmp_path.glob(".sfep-golden-v2-*")] == [replacement]


def test_golden_v2_publication_rejects_moved_out_scratch_before_first_path_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    publication_parent = tmp_path / "publication-parent"
    outside_parent = tmp_path / "outside-parent"
    publication_parent.mkdir()
    outside_parent.mkdir()
    displaced = outside_parent / "owned-scratch"
    replacement: Path | None = None
    real_stat = os.stat

    def move_outside_before_first_identity(
        path: object,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replacement
        if (
            replacement is None
            and isinstance(path, str)
            and path.startswith(globals_["_SCRATCH_PREFIX"])
            and dir_fd is not None
            and not follow_symlinks
        ):
            owned = publication_parent / path
            owned.rename(displaced)
            owned.mkdir(mode=0o700)
            replacement = owned
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(globals_["os"], "stat", move_outside_before_first_identity)
    output = publication_parent / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}

    with pytest.raises(namespace["GoldenV2SealError"], match="ambiguous|epoch"):
        globals_["_publish_bundle"](output, payloads, lambda: None)

    assert replacement is not None and replacement.is_dir()
    assert displaced.is_dir()
    assert not output.exists()
    assert not (publication_parent / globals_["_PUBLICATION_LOCK"]).exists()
    assert [
        path for path in publication_parent.glob(".sfep-golden-v2-*")
    ] == [replacement]


def test_golden_v2_first_open_replacement_is_ambiguous_and_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_open = os.open
    displaced = tmp_path / "owned-early-scratch"
    replacement: Path | None = None

    def replace_before_scratch_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replacement
        if (
            replacement is None
            and isinstance(path, str)
            and path.startswith(globals_["_SCRATCH_PREFIX"])
        ):
            owned = tmp_path / path
            owned.rename(displaced)
            owned.mkdir()
            replacement = owned
            raise OSError("injected scratch replacement open failure")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(globals_["os"], "open", replace_before_scratch_open)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(namespace["GoldenV2SealError"], match="ambiguous|epoch"):
        globals_["_publish_bundle"](output, payloads, lambda: None)

    assert replacement is not None and replacement.is_dir()
    assert displaced.is_dir()
    assert not output.exists()
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert [path for path in tmp_path.glob(".sfep-golden-v2-*")] == [replacement]


def test_golden_v2_publication_rolls_back_if_lock_unlink_initially_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_unlink = os.unlink
    failed = False

    def failing_first_lock_unlink(
        path: object,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal failed
        if path == globals_["_PUBLICATION_LOCK"] and not failed:
            failed = True
            raise OSError("injected lock unlink failure")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(globals_["os"], "unlink", failing_first_lock_unlink)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(OSError, match="injected lock unlink failure"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert failed
    assert not output.exists()
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []


def test_golden_v2_rollback_cleans_owned_alternate_scratch_by_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_install = globals_["_atomic_install_exclusive"]
    real_unlink = os.unlink
    occupied_name: str | None = None
    failed = False

    def commit_and_occupy_original(
        parent_fd: int,
        source_name: str,
        target_name: str,
    ) -> None:
        nonlocal occupied_name
        real_install(parent_fd, source_name, target_name)
        if target_name == "output" and occupied_name is None:
            os.mkdir(source_name, 0o700, dir_fd=parent_fd)
            occupied_name = source_name

    def failing_first_lock_unlink(
        path: object,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal failed
        if path == globals_["_PUBLICATION_LOCK"] and not failed:
            failed = True
            raise OSError("injected finalization failure")
        real_unlink(path, dir_fd=dir_fd)

    globals_["_atomic_install_exclusive"] = commit_and_occupy_original
    monkeypatch.setattr(globals_["os"], "unlink", failing_first_lock_unlink)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(OSError, match="injected finalization failure"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert occupied_name is not None
    assert (tmp_path / occupied_name).is_dir()
    assert not output.exists()
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert [path.name for path in tmp_path.glob(".sfep-golden-v2-*")] == [
        occupied_name
    ]


def test_golden_v2_publication_rolls_back_and_closes_parent_if_lock_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_open_directory = globals_["_open_directory"]
    real_unlink = os.unlink
    real_fsync = os.fsync
    real_close = os.close
    parent_descriptors: list[int] = []
    closed: list[int] = []
    lock_unlinked = False
    failed = False

    def recording_open_directory(path: Path, label: str) -> int:
        descriptor = real_open_directory(path, label)
        parent_descriptors.append(descriptor)
        return descriptor

    def recording_unlink(
        path: object,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal lock_unlinked
        real_unlink(path, dir_fd=dir_fd)
        if path == globals_["_PUBLICATION_LOCK"]:
            lock_unlinked = True

    def failing_lock_parent_fsync(descriptor: int) -> None:
        nonlocal failed
        if lock_unlinked and descriptor in parent_descriptors and not failed:
            failed = True
            raise OSError("injected post-lock-unlink parent fsync failure")
        real_fsync(descriptor)

    def recording_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    globals_["_open_directory"] = recording_open_directory
    monkeypatch.setattr(globals_["os"], "unlink", recording_unlink)
    monkeypatch.setattr(globals_["os"], "fsync", failing_lock_parent_fsync)
    monkeypatch.setattr(globals_["os"], "close", recording_close)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}
    with pytest.raises(OSError, match="post-lock-unlink parent fsync failure"):
        globals_["_publish_bundle"](output, payloads, lambda: None)
    assert failed and lock_unlinked
    assert parent_descriptors and all(fd in closed for fd in parent_descriptors)
    assert not output.exists()
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []


def test_golden_v2_durable_publication_ignores_descriptor_close_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_unlink_lock = globals_["_unlink_owned_lock"]
    real_fsync = os.fsync
    real_close = os.close
    lock_unlinked = False
    durable = False
    close_faults = 0

    def recording_unlink_lock(*args: object, **kwargs: object) -> None:
        nonlocal lock_unlinked
        real_unlink_lock(*args, **kwargs)
        lock_unlinked = True

    def recording_fsync(descriptor: int) -> None:
        nonlocal durable
        real_fsync(descriptor)
        if lock_unlinked:
            durable = True

    def close_then_report_diagnostic(descriptor: int) -> None:
        nonlocal close_faults
        real_close(descriptor)
        if durable:
            close_faults += 1
            raise OSError("injected durable close diagnostic")

    globals_["_unlink_owned_lock"] = recording_unlink_lock
    monkeypatch.setattr(globals_["os"], "fsync", recording_fsync)
    monkeypatch.setattr(globals_["os"], "close", close_then_report_diagnostic)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}

    status = globals_["_publish_bundle"](output, payloads, lambda: None)

    assert status == "created"
    assert durable and close_faults == 3
    assert {path.name for path in output.iterdir()} == set(_GOLDEN_V2_OUTPUT_NAMES)
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []


def test_golden_v2_predurable_cleanup_diagnostic_preserves_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_publish_bundle"].__globals__
    real_write = globals_["_write_file"]
    real_open = os.open
    real_close = os.close
    scratch_descriptor: int | None = None
    write_failed = False
    close_failed = False

    def recording_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal scratch_descriptor
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith(globals_["_SCRATCH_PREFIX"]):
            scratch_descriptor = descriptor
        return descriptor

    def fail_first_write(*args: object, **kwargs: object) -> None:
        nonlocal write_failed
        if not write_failed:
            write_failed = True
            raise namespace["GoldenV2SealError"]("primary publication failure")
        real_write(*args, **kwargs)

    def close_then_fail_once(descriptor: int) -> None:
        nonlocal close_failed
        real_close(descriptor)
        if descriptor == scratch_descriptor and not close_failed:
            close_failed = True
            raise OSError("secondary cleanup close diagnostic")

    globals_["_write_file"] = fail_first_write
    monkeypatch.setattr(globals_["os"], "open", recording_open)
    monkeypatch.setattr(globals_["os"], "close", close_then_fail_once)
    output = tmp_path / "output"
    payloads = {name: (name + "\n").encode() for name in _GOLDEN_V2_OUTPUT_NAMES}

    with pytest.raises(
        namespace["GoldenV2SealError"], match="primary publication failure"
    ) as captured:
        globals_["_publish_bundle"](output, payloads, lambda: None)

    assert write_failed and close_failed
    assert any(
        "secondary cleanup close diagnostic" in note
        for note in getattr(captured.value, "__notes__", ())
    )
    assert not output.exists()
    assert not (tmp_path / globals_["_PUBLICATION_LOCK"]).exists()
    assert list(tmp_path.glob(".sfep-golden-v2-*")) == []


@pytest.mark.parametrize("fault", ("write", "fsync"))
def test_golden_v2_write_file_preserves_primary_io_error_when_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_write_file"].__globals__
    real_open = os.open
    real_write = os.write
    real_fsync = os.fsync
    real_close = os.close
    real_fstat = os.fstat
    target_descriptor: int | None = None

    def recording_open(*args: object, **kwargs: object) -> int:
        nonlocal target_descriptor
        target_descriptor = real_open(*args, **kwargs)
        return target_descriptor

    def faulting_write(descriptor: int, payload: object) -> int:
        if fault == "write" and descriptor == target_descriptor:
            raise OSError("primary write failure")
        return real_write(descriptor, payload)

    def faulting_fsync(descriptor: int) -> None:
        if fault == "fsync" and descriptor == target_descriptor:
            raise OSError("primary fsync failure")
        real_fsync(descriptor)

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        if descriptor == target_descriptor:
            raise OSError("secondary write-file close diagnostic")

    monkeypatch.setattr(globals_["os"], "open", recording_open)
    monkeypatch.setattr(globals_["os"], "write", faulting_write)
    monkeypatch.setattr(globals_["os"], "fsync", faulting_fsync)
    monkeypatch.setattr(globals_["os"], "close", close_then_fail)

    with pytest.raises(OSError, match=f"primary {fault} failure") as captured:
        globals_["_write_file"](tmp_path / "payload", b"payload")

    assert any(
        "secondary write-file close diagnostic" in note
        for note in getattr(captured.value, "__notes__", ())
    )
    assert target_descriptor is not None
    with pytest.raises(OSError):
        real_fstat(target_descriptor)


@pytest.mark.parametrize("fault", ("read", "fstat"))
def test_golden_v2_read_file_at_preserves_primary_io_error_when_close_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_read_file_at"].__globals__
    payload = tmp_path / "payload"
    payload.write_bytes(b"payload")
    real_open = os.open
    real_read = os.read
    real_fstat = os.fstat
    real_close = os.close
    directory_descriptor = real_open(
        tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    target_descriptor: int | None = None
    target_fstats = 0

    def recording_open(*args: object, **kwargs: object) -> int:
        nonlocal target_descriptor
        target_descriptor = real_open(*args, **kwargs)
        return target_descriptor

    def faulting_read(descriptor: int, size: int) -> bytes:
        if fault == "read" and descriptor == target_descriptor:
            raise OSError("primary read failure")
        return real_read(descriptor, size)

    def faulting_fstat(descriptor: int) -> os.stat_result:
        nonlocal target_fstats
        if descriptor == target_descriptor:
            target_fstats += 1
            if fault == "fstat" and target_fstats == 2:
                raise OSError("primary fstat failure")
        return real_fstat(descriptor)

    def close_then_fail(descriptor: int) -> None:
        real_close(descriptor)
        if descriptor == target_descriptor:
            raise OSError("secondary read-file close diagnostic")

    monkeypatch.setattr(globals_["os"], "open", recording_open)
    monkeypatch.setattr(globals_["os"], "read", faulting_read)
    monkeypatch.setattr(globals_["os"], "fstat", faulting_fstat)
    monkeypatch.setattr(globals_["os"], "close", close_then_fail)
    try:
        with pytest.raises(OSError, match=f"primary {fault} failure") as captured:
            globals_["_read_file_at"](
                directory_descriptor, "payload", "fault-injected payload"
            )
    finally:
        real_close(directory_descriptor)

    assert any(
        "secondary read-file close diagnostic" in note
        for note in getattr(captured.value, "__notes__", ())
    )
    assert target_descriptor is not None
    with pytest.raises(OSError):
        real_fstat(target_descriptor)


def test_golden_v2_outer_parent_close_diagnostic_cannot_reverse_durable_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _golden_v2_seal_namespace()
    globals_ = namespace["_run"].__globals__
    real_open_directory = globals_["_open_directory"]
    real_close = os.close
    outer_parent_fd: int | None = None
    close_failed = False
    published = False

    def recording_open_directory(path: Path, label: str) -> int:
        nonlocal outer_parent_fd
        descriptor = real_open_directory(path, label)
        outer_parent_fd = descriptor
        return descriptor

    def durable_publish(*args: object, **kwargs: object) -> str:
        nonlocal published
        published = True
        return "created"

    def close_then_report_diagnostic(descriptor: int) -> None:
        nonlocal close_failed
        real_close(descriptor)
        if descriptor == outer_parent_fd and not close_failed:
            close_failed = True
            raise OSError("outer pinned-parent close diagnostic")

    globals_["_open_directory"] = recording_open_directory
    globals_["_validate_topology"] = lambda *args: None
    globals_["_capture_inputs"] = lambda *args: ({"v2/runtime": b"runtime"}, {})
    globals_["_verify_runtime_install"] = lambda *args: None
    globals_["_run_producer"] = lambda *args: tmp_path / "generated"
    globals_["_verify_generated"] = lambda *args: (
        "sha256:" + "a" * 64,
        {name: b"payload" for name in _GOLDEN_V2_OUTPUT_NAMES},
    )
    globals_["_publish_bundle"] = durable_publish
    monkeypatch.setattr(globals_["os"], "close", close_then_report_diagnostic)

    result = globals_["_run"](
        _GOLDEN_CONTRACT_ROOT,
        _GOLDEN_V2_CONFIG,
        _GOLDEN_V2_BUNDLE / "producer_runtime.json",
        tmp_path / "output",
    )

    assert result == ("created", "sha256:" + "a" * 64)
    assert published and close_failed


def test_golden_v2_producer_ignores_pythonpath_shadow_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shadow = tmp_path / "shadow"
    package = shadow / "equipment_quality"
    package.mkdir(parents=True)
    marker = tmp_path / "shadow-imported"
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cli.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('shadow', encoding='utf-8')\n"
        "raise SystemExit(91)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(shadow))
    result = _run_golden_v2_seal(
        _GOLDEN_CONTRACT_ROOT,
        _GOLDEN_V2_CONFIG,
        _GOLDEN_V2_BUNDLE / "producer_runtime.json",
        tmp_path / "output",
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_golden_v2_parent_authenticates_dependency_before_it_can_import(
    tmp_path: Path,
) -> None:
    runtime_python = _compat_runtime_python()
    dependency = (
        runtime_python.parents[1]
        / "lib/python3.12/site-packages/jsonschema/__init__.py"
    )
    original = dependency.read_bytes()
    original_digest = hashlib.sha256(original).hexdigest()
    marker = tmp_path / "dependency-imported"
    attack = (
        "from pathlib import Path as _AttackPath\n"
        f"_AttackPath({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
    ).encode("utf-8") + original
    try:
        dependency.write_bytes(attack)
        result = _run_golden_v2_seal(
            _GOLDEN_CONTRACT_ROOT,
            _GOLDEN_V2_CONFIG,
            _GOLDEN_V2_BUNDLE / "producer_runtime.json",
            tmp_path / "output",
        )
    finally:
        dependency.write_bytes(original)

    assert hashlib.sha256(dependency.read_bytes()).hexdigest() == original_digest
    assert result.returncode == 2
    assert "jsonschema" in result.stderr
    assert "RECORD" in result.stderr or "authenticate" in result.stderr
    assert not marker.exists()
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("child", ("producer", "schema"))
def test_golden_v2_child_reauthenticates_dependency_before_application_import(
    tmp_path: Path,
    child: str,
) -> None:
    runtime_python = _compat_runtime_python()
    marker = tmp_path / f"{child}-dependency-imported"
    child_output = tmp_path / "child-output"
    child_output.mkdir()
    harness = textwrap.dedent(
        r'''
        import hashlib, pathlib, runpy, subprocess, sys

        tool, runtime_path, fixture, config, contract, output, marker, child = sys.argv[1:]
        namespace = runpy.run_path(tool, run_name="child_race_harness")
        dependency = pathlib.Path(sys.executable).parent.parent / "lib/python3.12/site-packages/jsonschema/__init__.py"
        original = dependency.read_bytes()
        original_digest = hashlib.sha256(original).hexdigest()
        attack = (
            "from pathlib import Path as _AttackPath\n"
            + f"_AttackPath({marker!r}).write_text('imported', encoding='utf-8')\n"
        ).encode("utf-8") + original
        real_run = subprocess.run
        observed_error = None

        def mutate_between_parent_and_child(*args, **kwargs):
            dependency.write_bytes(attack)
            try:
                return real_run(*args, **kwargs)
            finally:
                dependency.write_bytes(original)

        namespace["_run_producer"].__globals__["subprocess"].run = mutate_between_parent_and_child
        try:
            if child == "producer":
                namespace["_run_producer"](
                    pathlib.Path(contract),
                    pathlib.Path(config),
                    pathlib.Path(runtime_path),
                    pathlib.Path(output),
                )
            else:
                names = namespace["_OUTPUT_NAMES"]
                payloads = {
                    name: (pathlib.Path(fixture) / name).read_bytes()
                    for name in names
                }
                namespace["_validate_output_schemas"](payloads)
        except namespace["GoldenV2SealError"] as error:
            observed_error = str(error)
        finally:
            dependency.write_bytes(original)
        if hashlib.sha256(dependency.read_bytes()).hexdigest() != original_digest:
            raise SystemExit(90)
        if pathlib.Path(marker).exists():
            print("MARKER_EXECUTED", file=sys.stderr)
            raise SystemExit(91)
        if observed_error is None:
            print("CHILD_DID_NOT_REJECT_TAMPER", file=sys.stderr)
            raise SystemExit(92)
        print(observed_error)
        '''
    )
    result = subprocess.run(
        [
            str(runtime_python),
            "-S",
            "-P",
            "-s",
            "-B",
            "-c",
            harness,
            str(_GOLDEN_V2_SEAL_TOOL),
            str(_GOLDEN_V2_BUNDLE / "producer_runtime.json"),
            str(_GOLDEN_V2_BUNDLE),
            str(_GOLDEN_V2_CONFIG),
            str(_GOLDEN_CONTRACT_ROOT),
            str(child_output),
            str(marker),
            child,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "Asia/Seoul",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "RECORD" in result.stdout or "authenticate" in result.stdout
    assert not marker.exists()


def test_golden_v2_child_never_self_authenticates_replaced_sealer_source(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "replacement-sealer-executed"
    child_output = tmp_path / "child-output"
    child_output.mkdir()
    harness = textwrap.dedent(
        r'''
        import pathlib, runpy, sys

        tool, runtime_path, config, contract, output, marker = sys.argv[1:]
        tool_path = pathlib.Path(tool)
        namespace = runpy.run_path(tool, run_name="source_race_harness")
        original = tool_path.read_bytes()
        attack = (
            "from pathlib import Path\n"
            "def _authenticated_child_main(envelope):\n"
            + f"    Path({marker!r}).write_text('executed', encoding='utf-8')\n"
        ).encode("utf-8")
        try:
            tool_path.write_bytes(attack)
            generated = namespace["_run_producer"](
                pathlib.Path(contract),
                pathlib.Path(config),
                pathlib.Path(runtime_path),
                pathlib.Path(output),
            )
        finally:
            tool_path.write_bytes(original)
        if pathlib.Path(marker).exists():
            print("REPLACEMENT_SEALER_EXECUTED", file=sys.stderr)
            raise SystemExit(91)
        if not generated.is_dir():
            print("RESIDENT_VERIFIER_DID_NOT_RUN", file=sys.stderr)
            raise SystemExit(92)
        print(generated.name)
        '''
    )
    result = subprocess.run(
        [
            str(_compat_runtime_python()),
            "-S",
            "-P",
            "-s",
            "-B",
            "-c",
            harness,
            str(_GOLDEN_V2_SEAL_TOOL),
            str(_GOLDEN_V2_BUNDLE / "producer_runtime.json"),
            str(_GOLDEN_V2_CONFIG),
            str(_GOLDEN_CONTRACT_ROOT),
            str(child_output),
            str(marker),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "Asia/Seoul",
        },
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_golden_v2_child_uses_resident_verifier_when_source_replaced_before_capture(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "pre-capture-replacement-executed"
    child_output = tmp_path / "child-output"
    child_output.mkdir()
    harness = textwrap.dedent(
        r'''
        import pathlib, sys

        tool, runtime_path, config, contract, output, marker = sys.argv[1:]
        tool_path = pathlib.Path(tool)
        original = tool_path.read_bytes()
        compiled = compile(original, tool, "exec")
        attack = (
            "from pathlib import Path\n"
            "def _authenticated_child_main(envelope):\n"
            + f"    Path({marker!r}).write_text('executed', encoding='utf-8')\n"
        ).encode("utf-8")
        namespace = {"__file__": tool, "__name__": "pre_capture_race_harness"}
        try:
            tool_path.write_bytes(attack)
            exec(compiled, namespace)
            generated = namespace["_run_producer"](
                pathlib.Path(contract),
                pathlib.Path(config),
                pathlib.Path(runtime_path),
                pathlib.Path(output),
            )
        except namespace.get("GoldenV2SealError", RuntimeError) as error:
            print(error)
            generated = None
        finally:
            tool_path.write_bytes(original)
        if pathlib.Path(marker).exists():
            print("PRE_CAPTURE_REPLACEMENT_EXECUTED", file=sys.stderr)
            raise SystemExit(91)
        if generated is None or not generated.is_dir():
            print("RESIDENT_VERIFIER_DID_NOT_RUN", file=sys.stderr)
            raise SystemExit(92)
        print(generated.name)
        '''
    )
    result = subprocess.run(
        [
            str(_compat_runtime_python()),
            "-S",
            "-P",
            "-s",
            "-B",
            "-c",
            harness,
            str(_GOLDEN_V2_SEAL_TOOL),
            str(_GOLDEN_V2_BUNDLE / "producer_runtime.json"),
            str(_GOLDEN_V2_CONFIG),
            str(_GOLDEN_CONTRACT_ROOT),
            str(child_output),
            str(marker),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "Asia/Seoul",
        },
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_golden_v2_parent_reauthenticates_immediately_after_schema_child(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "post-schema-dependency-imported"
    harness = textwrap.dedent(
        r'''
        import hashlib, json, pathlib, runpy, subprocess, sys

        tool, runtime_path, config, contract, output, marker = sys.argv[1:]
        namespace = runpy.run_path(tool, run_name="post_schema_race_harness")
        globals_ = namespace["_run"].__globals__
        dependency = pathlib.Path(sys.executable).parent.parent / "lib/python3.12/site-packages/jsonschema/__init__.py"
        original = dependency.read_bytes()
        original_digest = hashlib.sha256(original).hexdigest()
        attack = (
            "from pathlib import Path as _AttackPath\n"
            + f"_AttackPath({marker!r}).write_text('imported', encoding='utf-8')\n"
        ).encode("utf-8") + original
        real_run = subprocess.run
        state = {"schema_mutated": False, "publish_called": False}
        observed_error = None

        def mutate_after_schema(*args, **kwargs):
            result = real_run(*args, **kwargs)
            envelope = json.loads(kwargs["input"])
            if envelope.get("mode") == "schema":
                dependency.write_bytes(attack)
                state["schema_mutated"] = True
            return result

        def forbidden_publication(*args, **kwargs):
            state["publish_called"] = True
            raise namespace["GoldenV2SealError"]("publication reached after schema race")

        globals_["subprocess"].run = mutate_after_schema
        globals_["_publish_bundle"] = forbidden_publication
        try:
            namespace["_run"](
                pathlib.Path(contract),
                pathlib.Path(config),
                pathlib.Path(runtime_path),
                pathlib.Path(output),
            )
        except namespace["GoldenV2SealError"] as error:
            observed_error = str(error)
        finally:
            dependency.write_bytes(original)
        if hashlib.sha256(dependency.read_bytes()).hexdigest() != original_digest:
            raise SystemExit(90)
        if not state["schema_mutated"]:
            print("SCHEMA_CHILD_NOT_REACHED", file=sys.stderr)
            raise SystemExit(91)
        if state["publish_called"]:
            print("PUBLICATION_REACHED", file=sys.stderr)
            raise SystemExit(92)
        if pathlib.Path(marker).exists():
            print("MUTATED_DEPENDENCY_IMPORTED", file=sys.stderr)
            raise SystemExit(93)
        if observed_error is None:
            print("POST_SCHEMA_TAMPER_NOT_REJECTED", file=sys.stderr)
            raise SystemExit(94)
        print(observed_error)
        '''
    )
    result = subprocess.run(
        [
            str(_compat_runtime_python()),
            "-S",
            "-P",
            "-s",
            "-B",
            "-c",
            harness,
            str(_GOLDEN_V2_SEAL_TOOL),
            str(_GOLDEN_V2_BUNDLE / "producer_runtime.json"),
            str(_GOLDEN_V2_CONFIG),
            str(_GOLDEN_CONTRACT_ROOT),
            str(tmp_path / "output"),
            str(marker),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TZ": "Asia/Seoul",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "jsonschema" in result.stdout
    assert "RECORD" in result.stdout or "authenticate" in result.stdout
    assert not marker.exists()
    assert not (tmp_path / "output").exists()


def test_golden_oracles_ignore_valid_but_different_current_runtime_manifest(
    tmp_path: Path,
) -> None:
    current = ANALYSIS_ROOT / "producer_runtime.json"
    current_bytes = current.read_bytes()
    v1_runtime = _GOLDEN_CONTRACT_ROOT / "golden-bundle/producer_runtime.json"
    v2_runtime = _GOLDEN_V2_BUNDLE / "producer_runtime.json"
    assert current_bytes == v2_runtime.read_bytes()
    assert current_bytes != v1_runtime.read_bytes()
    try:
        current.write_bytes(v1_runtime.read_bytes())
        v1_output = tmp_path / "v1"
        v1_result = _run_golden_seal(
            _GOLDEN_CONTRACT_ROOT,
            ANALYSIS_ROOT / "analysis_config.json",
            v1_runtime,
            v1_output,
        )
        v2_output = tmp_path / "v2"
        v2_result = _run_golden_v2_seal(
            _GOLDEN_CONTRACT_ROOT,
            _GOLDEN_V2_CONFIG,
            v2_runtime,
            v2_output,
        )
    finally:
        current.write_bytes(current_bytes)

    assert v1_result.returncode == 0, v1_result.stderr
    assert v2_result.returncode == 0, v2_result.stderr
    assert (v1_output / "producer_runtime.json").read_bytes() == v1_runtime.read_bytes()
    assert (v2_output / "producer_runtime.json").read_bytes() == v2_runtime.read_bytes()
    assert current.read_bytes() == current_bytes
