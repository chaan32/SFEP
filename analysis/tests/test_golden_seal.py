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


def test_build_controls_umask_before_creating_and_rolls_back_work_directories(
    tmp_path: Path,
    build_python: Path,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    created_parent = tmp_path / "created-parent"
    work_root = created_parent / "created-work"
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    observed_modes: list[int] = []

    def failing_snapshot(_source_root: Path) -> dict[str, bytes]:
        observed_modes.extend(
            stat.S_IMODE(os.lstat(path).st_mode)
            for path in (created_parent, work_root)
        )
        raise error_type("injected failure after work-root creation")

    module_globals["_source_snapshot"] = failing_snapshot
    previous_umask = os.umask(0o777)
    try:
        with pytest.raises(error_type, match="injected failure"):
            module_globals["_run"](source, build_python, work_root)
    finally:
        os.umask(previous_umask)

    assert observed_modes == [0o755, 0o755]
    assert not created_parent.exists()


def test_build_rolls_back_a_directory_when_reopening_it_fails(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    created_parent = tmp_path / "created-parent"
    work_root = created_parent / "created-work"
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    real_open = os.open
    attempts = 0

    def failing_reopen(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal attempts
        if path == "created-parent":
            attempts += 1
            if attempts == 2:
                raise PermissionError("injected reopen failure")
        return real_open(path, flags, *arguments, **keywords)

    monkeypatch.setattr(module_globals["os"], "open", failing_reopen)

    with pytest.raises((error_type, PermissionError), match="reopen|opened safely"):
        module_globals["_run"](source, build_python, work_root)

    assert attempts == 2
    assert not created_parent.exists()


@pytest.mark.parametrize("operation", ("stat", "dup"))
def test_build_rolls_back_or_avoids_creation_when_ownership_capture_fails(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    created_parent = tmp_path / "created-parent"
    work_root = created_parent / "created-work"
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    injected = False

    if operation == "stat":
        real_operation = os.stat

        def failing_operation(
            path: os.PathLike[str] | str | bytes | int,
            *arguments: object,
            **keywords: object,
        ) -> os.stat_result:
            nonlocal injected
            if path == "created-parent":
                injected = True
                raise OSError("injected ownership stat failure")
            return real_operation(path, *arguments, **keywords)

    else:
        real_operation = os.dup

        def failing_operation(
            descriptor: int,
            *arguments: object,
            **keywords: object,
        ) -> int:
            nonlocal injected
            if not injected:
                injected = True
                raise OSError("injected parent descriptor duplication failure")
            return real_operation(descriptor, *arguments, **keywords)

    monkeypatch.setattr(module_globals["os"], operation, failing_operation)

    with pytest.raises((error_type, OSError)):
        module_globals["_run"](source, build_python, work_root)

    assert injected
    with pytest.raises(FileNotFoundError):
        os.lstat(created_parent)


@pytest.mark.parametrize("directory_name", ("wheel-a", "wheel-b"))
@pytest.mark.parametrize("reopen_result", ("failure", "replacement"))
def test_build_publication_never_removes_a_replacement_directory_before_reopen(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory_name: str,
    reopen_result: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = tmp_path / "work"
    detached = tmp_path / f"{directory_name}-detached-original"
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    real_open = os.open
    external_identity: tuple[int, int] | None = None

    def replacing_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal external_identity
        if external_identity is None and path == directory_name:
            target = work_root / directory_name
            target.rename(detached)
            target.mkdir()
            result = os.lstat(target)
            external_identity = result.st_dev, result.st_ino
            if reopen_result == "failure":
                raise PermissionError("injected output directory reopen failure")
        return real_open(path, flags, *arguments, **keywords)

    monkeypatch.setattr(module_globals["os"], "open", replacing_open)

    expected_message = "reopen" if reopen_result == "failure" else "changed before reopen"
    with pytest.raises(error_type, match=expected_message):
        module_globals["_run"](source, build_python, work_root)

    assert external_identity is not None
    current = os.lstat(work_root / directory_name)
    assert (current.st_dev, current.st_ino) == external_identity
    assert detached.is_dir()


@pytest.mark.parametrize("phase", ("work-root", "publication"))
def test_build_closes_a_reopened_directory_if_descriptor_authentication_fails(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = tmp_path / "created-parent" / "work"
    namespace = runpy.run_path(str(TOOLS_ROOT / "build_producer.py"))
    module_globals = namespace["_run"].__globals__
    error_type = module_globals["ProducerBuildError"]
    module_globals["_build_once"] = lambda *_arguments: b"identical test wheel"
    target_name = "created-parent" if phase == "work-root" else "wheel-a"
    real_open = os.open
    real_fstat = os.fstat
    real_close = os.close
    target_descriptor: int | None = None
    closed = False

    def recording_open(
        path: os.PathLike[str] | str | bytes | int,
        flags: int,
        *arguments: object,
        **keywords: object,
    ) -> int:
        nonlocal target_descriptor
        descriptor = real_open(path, flags, *arguments, **keywords)
        if target_descriptor is None and path == target_name:
            target_descriptor = descriptor
        return descriptor

    def failing_fstat(descriptor: int) -> os.stat_result:
        if descriptor == target_descriptor:
            raise OSError("injected directory descriptor authentication failure")
        return real_fstat(descriptor)

    def recording_close(descriptor: int) -> None:
        nonlocal closed
        if descriptor == target_descriptor:
            closed = True
        real_close(descriptor)

    monkeypatch.setattr(module_globals["os"], "open", recording_open)
    monkeypatch.setattr(module_globals["os"], "fstat", failing_fstat)
    monkeypatch.setattr(module_globals["os"], "close", recording_close)

    try:
        with pytest.raises((error_type, OSError), match="authentication|changed"):
            module_globals["_run"](source, build_python, work_root)
        assert target_descriptor is not None
        assert closed
    finally:
        if target_descriptor is not None and not closed:
            real_close(target_descriptor)


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
    work_root = tmp_path / "isolated-work"

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

    result = run_producer_build(source, build_python, tmp_path / "mutated-work")

    assert result.returncode == 0, result.stderr
    wheel = tmp_path / "mutated-work/wheel-a" / PRODUCER_FILENAME
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
    elif case == "case-aliased-source-overlap":
        aliased_source = source.with_name(source.name.swapcase())
        if not aliased_source.exists() or not os.path.samefile(
            aliased_source,
            source,
        ):
            pytest.skip("requires a case-insensitive filesystem")
        work_root = aliased_source / "work"
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
    work_root = tmp_path / "work"
    if case != "symlink-to-source":
        work_root.mkdir()
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
                work_root.mkdir()
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
    work_root = tmp_path / "work"
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
    assert not (work_root / "wheel-a").exists()
    assert not (work_root / "wheel-b").exists()


def test_build_rejects_replaced_output_instead_of_adopting_its_fingerprint(
    tmp_path: Path,
    build_python: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _copy_source_root(tmp_path / "analysis-copy")
    work_root = tmp_path / "work"
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
