"""Fail-closed runtime identity verification."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
from types import MappingProxyType
from types import ModuleType

import pytest

from factories.runtime import (
    HASH_SEED_PROBES,
    IMPORT_ROOTS,
    PACKAGE_SPECS,
    PIP_VERSION,
    PRODUCER_NAME,
    PROVENANCE_PATH,
    FakeDistribution,
    RuntimeFixture,
    build_runtime_fixture,
    independent_installed_code_tree,
    independent_tree_preimage,
    record_hash,
    sha256_uri,
)
from equipment_quality.deterministic import canonical_json_bytes
from equipment_quality.runtime_verify import (
    RuntimeIdentityError,
    RuntimeImportOrigin,
    installed_code_tree,
    verify_runtime,
)


def test_runtime_verifier_public_api_is_present() -> None:
    module_name = "equipment_quality.runtime_verify"
    assert importlib.util.find_spec(module_name) is not None

    runtime_verify = importlib.import_module(module_name)
    assert issubclass(runtime_verify.RuntimeIdentityError, RuntimeError)
    assert runtime_verify.RuntimeImportOrigin.__dataclass_params__.frozen
    assert runtime_verify.RuntimeEnvironment.__dataclass_params__.frozen
    assert runtime_verify.RuntimeIdentity.__dataclass_params__.frozen
    assert callable(runtime_verify.installed_code_tree)
    assert callable(runtime_verify.verify_runtime)


@pytest.fixture
def runtime_fixture(tmp_path: Path) -> RuntimeFixture:
    return build_runtime_fixture(tmp_path)


def _verify(fixture: RuntimeFixture):
    return verify_runtime(fixture.manifest_path, fixture.environment)


def _package_manifest(
    fixture: RuntimeFixture, name: str
) -> dict[str, object]:
    packages = fixture.manifest["packages"]
    assert isinstance(packages, list)
    return next(
        package
        for package in packages
        if isinstance(package, dict) and package["name"] == name
    )


def _replace_import(
    imports: tuple[RuntimeImportOrigin, ...],
    name: str,
    **changes: object,
) -> tuple[RuntimeImportOrigin, ...]:
    assert sum(item.name == name for item in imports) == 1
    return tuple(
        replace(item, **changes) if item.name == name else item
        for item in imports
    )


def test_happy_path_retains_exact_manifest_bytes_and_verified_vectors(
    runtime_fixture: RuntimeFixture,
) -> None:
    expected_bytes = runtime_fixture.manifest_path.read_bytes()

    identity = _verify(runtime_fixture)

    assert identity.manifest_bytes == expected_bytes
    assert identity.manifest_sha256 == sha256_uri(expected_bytes)
    assert identity.manifest["schemaVersion"] == "sfep-producer-runtime/v1"
    expected_names = {name for name, _version, _direct in PACKAGE_SPECS}
    expected_names.add(PRODUCER_NAME)
    assert set(identity.installed_code_trees) == expected_names
    assert identity.installed_code_trees[PRODUCER_NAME] == (
        runtime_fixture.manifest["producer"]["installedCodeTreeSha256"]
    )


def test_runtime_identity_is_recursively_immutable(
    runtime_fixture: RuntimeFixture,
) -> None:
    identity = _verify(runtime_fixture)

    with pytest.raises(FrozenInstanceError):
        identity.manifest_bytes = b"changed"  # type: ignore[misc]
    assert isinstance(identity.manifest, MappingProxyType)
    with pytest.raises(TypeError):
        identity.manifest["schemaVersion"] = "changed"  # type: ignore[index]
    platform_value = identity.manifest["platform"]
    assert isinstance(platform_value, MappingProxyType)
    with pytest.raises(TypeError):
        platform_value["system"] = "Linux"  # type: ignore[index]
    package_values = identity.manifest["packages"]
    assert isinstance(package_values, tuple)
    assert isinstance(package_values[0], MappingProxyType)
    with pytest.raises(TypeError):
        package_values[0]["name"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        identity.installed_code_trees["attrs"] = "changed"  # type: ignore[index]


def test_runtime_verification_is_repeatable(runtime_fixture: RuntimeFixture) -> None:
    first = _verify(runtime_fixture)
    second = _verify(runtime_fixture)

    assert first == second
    assert first is not second


def test_different_absolute_roots_and_direct_url_paths_have_identical_identity(
    tmp_path: Path,
) -> None:
    first = build_runtime_fixture(
        tmp_path / "first",
        direct_url_bytes=b'{"url":"file:///private/build/first"}\n',
    )
    second = build_runtime_fixture(
        tmp_path / "second",
        direct_url_bytes=b'{"url":"file:///private/build/second"}\n',
    )

    first_identity = _verify(first)
    second_identity = _verify(second)

    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert first_identity == second_identity


def test_manifest_is_read_once_before_environment_verification(
    runtime_fixture: RuntimeFixture,
) -> None:
    original_bytes = runtime_fixture.manifest_path.read_bytes()
    producer = runtime_fixture.distributions[PRODUCER_NAME]
    producer.set_record_read_callback(
        lambda: runtime_fixture.manifest_path.write_bytes(b"changed after snapshot\n")
    )

    identity = _verify(runtime_fixture)

    assert identity.manifest_bytes == original_bytes
    assert runtime_fixture.manifest_path.read_bytes() == b"changed after snapshot\n"
    assert producer.record_read_count == 1


def test_installed_code_tree_matches_independent_exact_line_vector(
    runtime_fixture: RuntimeFixture,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    stem = "attrs"
    metadata_path = f"{distribution.dist_info}/METADATA"
    package_path = f"{stem}/__init__.py"
    legacy_package_path = "attr/__init__.py"
    expected_lines = sorted(
        (
            legacy_package_path.encode("utf-8")
            + b"=sha256:"
            + hashlib.sha256(
                distribution.locate_file(legacy_package_path).read_bytes()
            )
            .hexdigest()
            .encode("ascii")
            + b"\n",
            package_path.encode("utf-8")
            + b"=sha256:"
            + hashlib.sha256(distribution.locate_file(package_path).read_bytes())
            .hexdigest()
            .encode("ascii")
            + b"\n",
            metadata_path.encode("utf-8")
            + b"=sha256:"
            + hashlib.sha256(distribution.locate_file(metadata_path).read_bytes())
            .hexdigest()
            .encode("ascii")
            + b"\n",
        )
    )
    literal_preimage = b"".join(expected_lines)

    assert independent_tree_preimage(distribution) == literal_preimage
    assert installed_code_tree(distribution) == sha256_uri(literal_preimage)
    assert installed_code_tree(distribution) == independent_installed_code_tree(
        distribution
    )


@pytest.mark.parametrize(
    "excluded_path",
    [
        "attrs/__pycache__/generated.cpython-312.pyc",
        "attrs/generated.pyc",
        "attrs-25.3.0.dist-info/INSTALLER",
        "attrs-25.3.0.dist-info/direct_url.json",
        "attrs-25.3.0.dist-info/REQUESTED",
        "../../../bin/attrs",
    ],
)
def test_tree_exclusions_are_verified_but_do_not_change_digest(
    runtime_fixture: RuntimeFixture,
    excluded_path: str,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    original = installed_code_tree(distribution)

    distribution.replace_file(excluded_path, b"different excluded bytes\n")

    assert installed_code_tree(distribution) == original


def test_record_order_and_directory_entries_do_not_change_tree_digest(
    runtime_fixture: RuntimeFixture,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    original = installed_code_tree(distribution)
    directory = distribution.locate_file("attrs/data")
    directory.mkdir(parents=True)
    rows = distribution.rows()
    rows.insert(0, ["attrs/data", "", ""])
    record_row = rows.pop(-1)
    rows.reverse()
    rows.append(record_row)
    distribution.replace_rows(rows)

    assert installed_code_tree(distribution) == original


def test_pip_generated_unhashed_bytecode_record_rows_are_excluded(
    runtime_fixture: RuntimeFixture,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    original = installed_code_tree(distribution)
    rows = distribution.rows()
    generated = next(
        row for row in rows if "__pycache__" in row[0] and row[0].endswith(".pyc")
    )
    generated[1:] = ["", ""]
    distribution.replace_rows(rows)

    assert installed_code_tree(distribution) == original


def test_generated_bin_record_rejects_a_symlinked_parent(
    runtime_fixture: RuntimeFixture,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    script = distribution.locate_file("../../../bin/attrs")
    bin_directory = script.parent
    real_directory = bin_directory.with_name("bin-real")
    bin_directory.rename(real_directory)
    bin_directory.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(RuntimeIdentityError):
        installed_code_tree(distribution)


@pytest.mark.parametrize(
    "codepoint",
    (*range(32), 127),
    ids=lambda value: f"U+{value:04X}",
)
def test_record_paths_reject_every_c0_boundary_and_del_character(
    runtime_fixture: RuntimeFixture,
    codepoint: int,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    unsafe_path = f"attrs/control{chr(codepoint)}.py"
    if "\x00" in unsafe_path:
        distribution.add_row(unsafe_path, "", "")
    else:
        distribution.replace_file(unsafe_path, b"unsafe path payload\n")

    with pytest.raises(RuntimeIdentityError):
        installed_code_tree(distribution)


def test_record_path_control_rejection_prevents_independent_preimage_collision(
    tmp_path: Path,
) -> None:
    attack = FakeDistribution(
        tmp_path / "attack/lib/python3.12/site-packages",
        "attrs",
        "25.3.0",
    )
    normal = FakeDistribution(
        tmp_path / "normal/lib/python3.12/site-packages",
        "attrs",
        "25.3.0",
    )
    first_bytes = b"first collision line\n"
    second_bytes = b"second collision line\n"
    first_hex = hashlib.sha256(first_bytes).hexdigest()
    attack.replace_file(
        f"attrs/a=sha256:{first_hex}\nattrs/b",
        second_bytes,
    )
    normal.replace_file("attrs/a", first_bytes)
    normal.replace_file("attrs/b", second_bytes)

    assert independent_tree_preimage(attack) == independent_tree_preimage(normal)
    assert installed_code_tree(normal) == independent_installed_code_tree(normal)
    with pytest.raises(RuntimeIdentityError):
        installed_code_tree(attack)


@pytest.mark.parametrize(
    "unsupported_path",
    [
        "../../bin/attrs",
        "../../../../bin/attrs",
        "../../../bin/tools/attrs",
        "../../../bin/../attrs",
    ],
)
def test_generated_script_exclusion_requires_exact_darwin_venv_shape(
    runtime_fixture: RuntimeFixture,
    unsupported_path: str,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    distribution.replace_file(unsupported_path, b"#!/bin/sh\n")

    with pytest.raises(RuntimeIdentityError):
        installed_code_tree(distribution)


def test_generated_script_exclusion_rejects_a_directory_target(
    runtime_fixture: RuntimeFixture,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    script_path = "../../../bin/attrs"
    distribution.remove_record_path(script_path, unlink=True)
    distribution.locate_file(script_path).mkdir()
    distribution.add_row(script_path, "", "")

    with pytest.raises(RuntimeIdentityError):
        installed_code_tree(distribution)


def test_record_locate_file_must_equal_the_checked_base_path_mapping(
    runtime_fixture: RuntimeFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    raw_path = "attrs/__init__.py"
    expected = distribution.locate_file(raw_path)
    arbitrary = runtime_fixture.root / "adapter-selected.py"
    arbitrary.write_bytes(expected.read_bytes())
    original_locate = distribution.locate_file

    def relocated(path: str | PurePosixPath) -> Path:
        if str(path) == raw_path:
            return arbitrary
        return original_locate(path)

    monkeypatch.setattr(distribution, "locate_file", relocated)

    with pytest.raises(RuntimeIdentityError):
        installed_code_tree(distribution)


def test_record_locate_file_rejects_symlink_intermediate_lexical_alias(
    runtime_fixture: RuntimeFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    raw_path = "attrs/__init__.py"
    expected = distribution.locate_file(raw_path)
    outside = runtime_fixture.root / "outside"
    arbitrary = outside / raw_path
    arbitrary.parent.mkdir(parents=True)
    arbitrary.write_bytes(expected.read_bytes())
    intermediate = outside / "child"
    intermediate.mkdir()
    adapter_link = distribution.root / "adapter-link"
    adapter_link.symlink_to(intermediate, target_is_directory=True)
    original_locate = distribution.locate_file

    def relocated(path: str | PurePosixPath) -> Path:
        if str(path) == raw_path:
            return adapter_link / ".." / raw_path
        return original_locate(path)

    monkeypatch.setattr(distribution, "locate_file", relocated)

    with pytest.raises(RuntimeIdentityError):
        installed_code_tree(distribution)


def test_record_change_after_distribution_read_is_rejected(
    runtime_fixture: RuntimeFixture,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    rows = distribution.rows()
    distribution.set_record_read_callback(
        lambda: distribution.replace_rows(reversed(rows))
    )

    with pytest.raises(RuntimeIdentityError):
        installed_code_tree(distribution)


@pytest.mark.parametrize(
    "case",
    [
        "missing-record",
        "malformed-row",
        "duplicate-path",
        "parent-escape",
        "absolute-path",
        "backslash-path",
        "empty-path",
        "missing-file",
        "unsupported-hash",
        "missing-hash",
        "invalid-hash",
        "missing-size",
        "invalid-size",
        "hash-mismatch",
        "size-mismatch",
        "symlink",
    ],
)
def test_installed_code_tree_rejects_record_path_hash_size_and_file_failures(
    runtime_fixture: RuntimeFixture,
    case: str,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    rows = distribution.rows()
    payload_index = next(index for index, row in enumerate(rows) if row[0] == "attrs/__init__.py")
    payload = rows[payload_index]

    if case == "missing-record":
        distribution.remove_record_file()
    elif case == "malformed-row":
        rows[payload_index] = payload + ["fourth"]
        distribution.replace_rows(rows)
    elif case == "duplicate-path":
        rows.insert(payload_index, list(payload))
        distribution.replace_rows(rows)
    elif case == "parent-escape":
        rows[payload_index][0] = "../outside.py"
        distribution.replace_rows(rows)
    elif case == "absolute-path":
        rows[payload_index][0] = "/absolute.py"
        distribution.replace_rows(rows)
    elif case == "backslash-path":
        rows[payload_index][0] = "attrs\\outside.py"
        distribution.replace_rows(rows)
    elif case == "empty-path":
        rows[payload_index][0] = ""
        distribution.replace_rows(rows)
    elif case == "missing-file":
        distribution.locate_file(payload[0]).unlink()
    elif case == "unsupported-hash":
        rows[payload_index][1] = "md5=AAAAAAAAAAAAAAAAAAAAAA"
        distribution.replace_rows(rows)
    elif case == "missing-hash":
        rows[payload_index][1] = ""
        distribution.replace_rows(rows)
    elif case == "invalid-hash":
        rows[payload_index][1] = "sha256=not-base64"
        distribution.replace_rows(rows)
    elif case == "missing-size":
        rows[payload_index][2] = ""
        distribution.replace_rows(rows)
    elif case == "invalid-size":
        rows[payload_index][2] = "-1"
        distribution.replace_rows(rows)
    elif case == "hash-mismatch":
        rows[payload_index][1] = record_hash(b"other")
        distribution.replace_rows(rows)
    elif case == "size-mismatch":
        rows[payload_index][2] = str(int(payload[2]) + 1)
        distribution.replace_rows(rows)
    elif case == "symlink":
        target = runtime_fixture.root / "symlink-target.py"
        target.write_bytes(distribution.locate_file(payload[0]).read_bytes())
        distribution.make_symlink(payload[0], target)
    else:  # pragma: no cover - parameter list is closed above
        raise AssertionError(case)

    with pytest.raises(RuntimeIdentityError):
        installed_code_tree(distribution)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("bom", lambda valid: b"\xef\xbb\xbf" + valid),
        ("invalid-utf8", lambda valid: valid[:-1] + b"\xff\n"),
        (
            "duplicate-member",
            lambda valid: (
                b'{"schemaVersion":"sfep-producer-runtime/v1",' + valid[1:]
            ),
        ),
        (
            "non-finite",
            lambda valid: valid.replace(b'"pipVersion":"25.1.1"', b'"pipVersion":NaN'),
        ),
        (
            "noncanonical-whitespace",
            lambda valid: json.dumps(json.loads(valid), indent=2).encode("utf-8") + b"\n",
        ),
        ("missing-terminal-lf", lambda valid: valid[:-1]),
        ("trailing-data", lambda valid: valid + b"{}\n"),
    ],
)
def test_manifest_rejects_strict_json_and_canonical_byte_failures(
    runtime_fixture: RuntimeFixture,
    name: str,
    payload,
) -> None:
    del name
    valid = runtime_fixture.manifest_path.read_bytes()
    runtime_fixture.set_manifest_bytes(payload(valid))

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize("mutation", ["missing-required", "extra-member", "wrong-constant"])
def test_manifest_rejects_schema_failures(
    runtime_fixture: RuntimeFixture,
    mutation: str,
) -> None:
    if mutation == "missing-required":
        del runtime_fixture.manifest["platform"]
    elif mutation == "extra-member":
        runtime_fixture.manifest["unapproved"] = True
    else:
        runtime_fixture.manifest["schemaVersion"] = "future"
    runtime_fixture.write_manifest()

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_manifest_rejects_bounded_oversize_input(
    runtime_fixture: RuntimeFixture,
) -> None:
    runtime_fixture.set_manifest_bytes(b" " * (1024 * 1024 + 1))

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "unreadable"])
def test_manifest_rejects_non_regular_symlink_and_unreadable_paths(
    runtime_fixture: RuntimeFixture,
    kind: str,
) -> None:
    manifest_path = runtime_fixture.manifest_path
    if kind == "missing":
        manifest_path.unlink()
    elif kind == "directory":
        manifest_path.unlink()
        manifest_path.mkdir()
    elif kind == "symlink":
        target = runtime_fixture.root / "manifest-target.json"
        target.write_bytes(manifest_path.read_bytes())
        manifest_path.unlink()
        manifest_path.symlink_to(target)
    else:
        manifest_path.chmod(0)
    try:
        with pytest.raises(RuntimeIdentityError):
            _verify(runtime_fixture)
    finally:
        if kind == "unreadable":
            manifest_path.chmod(0o600)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("platform_system", "Linux"),
        ("platform_machine", "x86_64"),
        ("macos_product_version", "15.6.2"),
        ("sysconfig_platform", "macosx-15.0-arm64"),
        ("python_implementation", "PyPy"),
        ("python_version", "3.12.11"),
        ("python_build", "different build"),
        ("python_cache_tag", "cpython-313"),
        ("python_soabi", "cpython-312-other"),
        ("python_hash_seed", None),
        ("python_hash_seed", "1"),
        ("timezone", None),
        ("timezone", "UTC"),
    ],
)
def test_runtime_rejects_platform_python_and_environment_policy_mutations(
    runtime_fixture: RuntimeFixture,
    field: str,
    changed: object,
) -> None:
    runtime_fixture.replace_environment(**{field: changed})

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize(
    "probes",
    [
        (HASH_SEED_PROBES[0] + 1, HASH_SEED_PROBES[1]),
        (HASH_SEED_PROBES[0], HASH_SEED_PROBES[1] + 1),
        tuple(reversed(HASH_SEED_PROBES)),
    ],
)
def test_runtime_authenticates_active_hash_seed_with_pinned_probes(
    runtime_fixture: RuntimeFixture,
    probes: tuple[int, int],
) -> None:
    assert runtime_fixture.environment.python_hash_seed == "0"
    runtime_fixture.replace_environment(python_hash_probes=probes)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_import_root_map_is_exact_and_extension_and_synthetic_modules_bind(
    runtime_fixture: RuntimeFixture,
) -> None:
    expected_roots = {
        root
        for distribution_roots in IMPORT_ROOTS.values()
        for root in distribution_roots
    }

    identity = _verify(runtime_fixture)

    assert identity.manifest_bytes == runtime_fixture.manifest_path.read_bytes()
    assert {item.name for item in runtime_fixture.environment.resolved_imports} == (
        expected_roots
    )
    loaded = {item.name: item for item in runtime_fixture.environment.loaded_imports}
    assert loaded["numpy._core._multiarray_umath"].origin.name.endswith(".so")
    assert loaded["six.moves"].origin is None
    assert loaded["six.moves"].loader_module == "six"


@pytest.mark.parametrize("snapshot_field", ["resolved_imports", "loaded_imports"])
def test_runtime_import_snapshots_are_frozen_unique_and_ordered(
    runtime_fixture: RuntimeFixture,
    snapshot_field: str,
) -> None:
    snapshot = getattr(runtime_fixture.environment, snapshot_field)
    assert isinstance(snapshot, tuple)
    runtime_fixture.replace_environment(**{snapshot_field: tuple(reversed(snapshot))})

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize(
    ("snapshot_field", "name", "changes"),
    [
        (
            "resolved_imports",
            "attrs",
            {"origin": Path("/tmp/source-shadow/attrs/__init__.py")},
        ),
        (
            "loaded_imports",
            "jsonschema",
            {
                "origin": Path("/tmp/pythonpath-shadow/jsonschema/__init__.py"),
                "module_file": Path("/tmp/pythonpath-shadow/jsonschema/__init__.py"),
            },
        ),
    ],
)
def test_runtime_rejects_resolved_and_loaded_source_tree_shadows(
    runtime_fixture: RuntimeFixture,
    snapshot_field: str,
    name: str,
    changes: dict[str, object],
) -> None:
    snapshot = getattr(runtime_fixture.environment, snapshot_field)
    runtime_fixture.replace_environment(
        **{snapshot_field: _replace_import(snapshot, name, **changes)}
    )

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_rejects_import_origin_symlink_intermediate_lexical_alias(
    runtime_fixture: RuntimeFixture,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    expected = distribution.locate_file("attrs/__init__.py")
    outside = runtime_fixture.root / "import-outside"
    arbitrary = outside / "attrs/__init__.py"
    arbitrary.parent.mkdir(parents=True)
    arbitrary.write_bytes(expected.read_bytes())
    intermediate = outside / "child"
    intermediate.mkdir()
    adapter_link = distribution.root / "import-link"
    adapter_link.symlink_to(intermediate, target_is_directory=True)
    aliased_origin = adapter_link / ".." / "attrs/__init__.py"
    resolved = _replace_import(
        runtime_fixture.environment.resolved_imports,
        "attrs",
        origin=aliased_origin,
        package_locations=(aliased_origin.parent,),
    )
    runtime_fixture.replace_environment(resolved_imports=resolved)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_rejects_import_origin_backed_by_the_wrong_distribution(
    runtime_fixture: RuntimeFixture,
) -> None:
    wrong_origin = runtime_fixture.distributions["attrs"].locate_file(
        "attrs/__init__.py"
    )
    snapshot = _replace_import(
        runtime_fixture.environment.resolved_imports,
        "jsonschema",
        origin=wrong_origin,
        package_locations=(wrong_origin.parent,),
    )
    runtime_fixture.replace_environment(resolved_imports=snapshot)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_rejects_record_backed_non_module_origin(
    runtime_fixture: RuntimeFixture,
) -> None:
    distribution = runtime_fixture.distributions["jsonschema"]
    metadata_origin = distribution.locate_file(f"{distribution.dist_info}/METADATA")
    snapshot = _replace_import(
        runtime_fixture.environment.resolved_imports,
        "jsonschema",
        origin=metadata_origin,
        package_locations=(),
    )
    runtime_fixture.replace_environment(resolved_imports=snapshot)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_rejects_package_initializer_without_its_package_location(
    runtime_fixture: RuntimeFixture,
) -> None:
    snapshot = _replace_import(
        runtime_fixture.environment.resolved_imports,
        "jsonschema",
        package_locations=(),
    )
    runtime_fixture.replace_environment(resolved_imports=snapshot)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_rejects_module_file_disagreement_and_unbacked_package_location(
    runtime_fixture: RuntimeFixture,
) -> None:
    original_loaded = runtime_fixture.environment.loaded_imports
    shadow = runtime_fixture.root / "shadow/jsonschema/__init__.py"
    shadow.parent.mkdir(parents=True)
    shadow.write_bytes(b"shadow\n")
    loaded = _replace_import(
        runtime_fixture.environment.loaded_imports,
        "jsonschema",
        module_file=shadow,
    )
    runtime_fixture.replace_environment(loaded_imports=loaded)
    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)

    resolved = _replace_import(
        runtime_fixture.environment.resolved_imports,
        "jsonschema",
        package_locations=(shadow.parent,),
    )
    runtime_fixture.replace_environment(
        resolved_imports=resolved,
        loaded_imports=original_loaded,
    )
    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_rejects_loaded_module_path_that_disagrees_with_its_spec(
    runtime_fixture: RuntimeFixture,
) -> None:
    shadow = runtime_fixture.root / "shadow/jsonschema"
    shadow.mkdir(parents=True)
    loaded = _replace_import(
        runtime_fixture.environment.loaded_imports,
        "jsonschema",
        module_package_locations=(shadow,),
    )
    runtime_fixture.replace_environment(loaded_imports=loaded)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_rejects_loaded_module_file_symlink_intermediate_lexical_alias(
    runtime_fixture: RuntimeFixture,
) -> None:
    distribution = runtime_fixture.distributions["attrs"]
    expected = distribution.locate_file("attrs/__init__.py")
    outside = runtime_fixture.root / "module-file-outside"
    arbitrary = outside / "attrs/__init__.py"
    arbitrary.parent.mkdir(parents=True)
    arbitrary.write_bytes(expected.read_bytes())
    intermediate = outside / "child"
    intermediate.mkdir()
    adapter_link = distribution.root / "module-file-link"
    adapter_link.symlink_to(intermediate, target_is_directory=True)
    aliased_file = adapter_link / ".." / "attrs/__init__.py"
    loaded = _replace_import(
        runtime_fixture.environment.loaded_imports,
        "attrs",
        module_file=aliased_file,
    )
    runtime_fixture.replace_environment(loaded_imports=loaded)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize(
    ("name", "changes"),
    [
        (
            "attrs",
            {
                "origin": None,
                "module_file": None,
                "loader_module": "_frozen_importlib_external",
            },
        ),
        ("six.moves", {"loader_module": "importlib"}),
    ],
)
def test_runtime_rejects_namespace_roots_and_unbacked_synthetic_loaders(
    runtime_fixture: RuntimeFixture,
    name: str,
    changes: dict[str, object],
) -> None:
    field = "resolved_imports" if name == "attrs" else "loaded_imports"
    snapshot = getattr(runtime_fixture.environment, field)
    runtime_fixture.replace_environment(
        **{field: _replace_import(snapshot, name, **changes)}
    )

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_rejects_originless_child_namespace_with_a_package_loader(
    runtime_fixture: RuntimeFixture,
) -> None:
    injected = RuntimeImportOrigin(
        name="jsonschema.namespace",
        origin=None,
        module_file=None,
        package_locations=(),
        module_package_locations=(),
        loader_module="jsonschema",
    )
    loaded = tuple(sorted(
        (*runtime_fixture.environment.loaded_imports, injected),
        key=lambda item: item.name,
    ))
    runtime_fixture.replace_environment(loaded_imports=loaded)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_production_import_capture_records_resolutions_and_all_loaded_modules(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_verify = importlib.import_module("equipment_quality.runtime_verify")
    probe = ModuleType("attrs.review_probe")
    probe_origin = tmp_path / "attrs/review_probe.py"
    probe.__file__ = str(probe_origin)
    probe.__spec__ = importlib.machinery.ModuleSpec(
        probe.__name__,
        loader=None,
        origin=str(probe_origin),
    )
    monkeypatch.setitem(sys.modules, probe.__name__, probe)

    resolved, loaded = runtime_verify._capture_import_state()

    assert {item.name for item in resolved} == {
        root for roots in IMPORT_ROOTS.values() for root in roots
    }
    assert any(item.name == probe.__name__ for item in loaded)


def test_production_import_capture_records_the_active_loaded_module_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_verify = importlib.import_module("equipment_quality.runtime_verify")
    module = sys.modules["jsonschema"]
    shadow = tmp_path / "pythonpath-shadow/jsonschema"
    shadow.mkdir(parents=True)
    monkeypatch.setattr(module, "__path__", [str(shadow)])

    _resolved, loaded = runtime_verify._capture_import_state()
    captured = next(item for item in loaded if item.name == "jsonschema")

    assert captured.module_package_locations == (shadow,)
    assert captured.module_package_locations != captured.package_locations


def test_runtime_hashes_realpath_interpreter_bytes(
    runtime_fixture: RuntimeFixture,
) -> None:
    target = runtime_fixture.root / "real-python"
    original = runtime_fixture.environment.executable.read_bytes()
    target.write_bytes(original)
    link = runtime_fixture.root / "python-link"
    link.symlink_to(target)
    runtime_fixture.replace_environment(executable=link)
    assert _verify(runtime_fixture).manifest_bytes == runtime_fixture.manifest_path.read_bytes()

    target.write_bytes(original + b"mutation")
    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize(
    "mutation",
    [
        "reordered",
        "direct-flag",
        "missing",
        "extra",
        "duplicate",
        "normalized-collision",
    ],
)
def test_manifest_package_inventory_order_directness_and_uniqueness_are_exact(
    runtime_fixture: RuntimeFixture,
    mutation: str,
) -> None:
    packages = runtime_fixture.manifest["packages"]
    assert isinstance(packages, list)
    if mutation == "reordered":
        packages[0], packages[1] = packages[1], packages[0]
    elif mutation == "direct-flag":
        packages[0]["direct"] = True
    elif mutation == "missing":
        packages.pop()
    elif mutation == "extra":
        extra = copy.deepcopy(packages[-1])
        extra["name"] = "wheel"
        packages.append(extra)
    elif mutation == "duplicate":
        packages[-1] = copy.deepcopy(packages[0])
    else:
        collision = copy.deepcopy(packages[-1])
        collision["name"] = "typing_extensions"
        packages.insert(-1, collision)
    runtime_fixture.write_manifest()

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-package",
        "extra-package",
        "duplicate-name",
        "normalized-collision",
        "missing-pip",
        "missing-producer",
    ],
)
def test_observed_distribution_inventory_is_exact(
    runtime_fixture: RuntimeFixture,
    mutation: str,
) -> None:
    distributions = list(runtime_fixture.environment.distributions)
    if mutation == "missing-package":
        distributions = [dist for dist in distributions if dist.metadata["Name"] != "attrs"]
    elif mutation == "extra-package":
        distributions.append(FakeDistribution(
            runtime_fixture.root / "extra/lib/python3.12/site-packages",
            "wheel",
            "0.45.1",
        ))
    elif mutation == "duplicate-name":
        distributions.append(runtime_fixture.distributions["attrs"])
    elif mutation == "normalized-collision":
        distributions.append(FakeDistribution(
            runtime_fixture.root / "collision/lib/python3.12/site-packages",
            "typing_extensions",
            "4.14.0",
        ))
    elif mutation == "missing-pip":
        distributions = [dist for dist in distributions if dist.metadata["Name"] != "pip"]
    else:
        distributions = [
            dist for dist in distributions if dist.metadata["Name"] != PRODUCER_NAME
        ]
    runtime_fixture.replace_environment(distributions=tuple(distributions))

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_observed_distribution_names_use_pep503_canonicalization(
    runtime_fixture: RuntimeFixture,
) -> None:
    runtime_fixture.distributions["attrs"].set_name("Attrs")
    runtime_fixture.distributions["jsonschema-specifications"].set_name(
        "jsonschema_specifications"
    )
    runtime_fixture.distributions[PRODUCER_NAME].set_name("SFEP.Equipment_Quality")
    runtime_fixture.distributions["pip"].set_name("PIP")

    assert _verify(runtime_fixture).manifest_bytes == (
        runtime_fixture.manifest_path.read_bytes()
    )


@pytest.mark.parametrize("name", ["attrs", "numpy", PRODUCER_NAME])
def test_runtime_rejects_installed_distribution_version_mutation(
    runtime_fixture: RuntimeFixture,
    name: str,
) -> None:
    runtime_fixture.distributions[name].set_version("99.0")

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize("name", ["attrs", "numpy", PRODUCER_NAME])
def test_runtime_rejects_installed_tree_mutation_even_with_refreshed_record(
    runtime_fixture: RuntimeFixture,
    name: str,
) -> None:
    distribution = runtime_fixture.distributions[name]
    included_path = next(
        row[0]
        for row in distribution.rows()
        if row[0].endswith("/__init__.py")
    )
    distribution.replace_file(included_path, b"mutated installed code\n")

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_rejects_record_hash_mutation_before_tree_comparison(
    runtime_fixture: RuntimeFixture,
) -> None:
    distribution = runtime_fixture.distributions["pandas"]
    path = next(row[0] for row in distribution.rows() if row[0].endswith("/__init__.py"))
    distribution.replace_file(path, b"unrecorded mutation\n", refresh_record=False)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_runtime_enforces_logical_producer_distribution_mapping(
    runtime_fixture: RuntimeFixture,
) -> None:
    runtime_fixture.distributions[PRODUCER_NAME].set_name("equipment-quality")

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_pip_is_the_only_separate_inventory_exclusion_and_version_is_bound(
    runtime_fixture: RuntimeFixture,
) -> None:
    assert _verify(runtime_fixture).manifest["pipVersion"] == PIP_VERSION
    runtime_fixture.distributions["pip"].set_version("25.1.2")

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_producer_provenance_must_be_record_backed(
    runtime_fixture: RuntimeFixture,
) -> None:
    producer = runtime_fixture.distributions[PRODUCER_NAME]
    producer.remove_record_path(PROVENANCE_PATH)
    runtime_fixture.refresh_tree(PRODUCER_NAME)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize("case", ["missing-file", "malformed", "bom", "noncanonical"])
def test_producer_provenance_rejects_missing_and_noncanonical_bytes(
    runtime_fixture: RuntimeFixture,
    case: str,
) -> None:
    producer = runtime_fixture.distributions[PRODUCER_NAME]
    if case == "missing-file":
        producer.remove_record_path(PROVENANCE_PATH, unlink=True)
    else:
        if case == "malformed":
            payload = b"{\n"
        elif case == "bom":
            payload = b"\xef\xbb\xbf" + canonical_json_bytes(runtime_fixture.provenance)
        else:
            payload = json.dumps(runtime_fixture.provenance, indent=2).encode("utf-8") + b"\n"
        producer.replace_file(PROVENANCE_PATH, payload)
    runtime_fixture.refresh_tree(PRODUCER_NAME)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize(
    ("case", "field", "value"),
    [
        ("extra", "extra", True),
        ("schema", "schemaVersion", "future"),
        ("producer-name", "producerName", "other"),
        ("distribution-name", "distributionName", "equipment-quality"),
        ("version", "version", "1.0.1"),
        ("source", "sourceSha256", "sha256:" + "f" * 64),
        ("epoch", "sourceDateEpoch", "1735689601"),
        ("requirements", "requirementsLockSha256", "sha256:" + "e" * 64),
    ],
)
def test_producer_provenance_fields_constants_and_matches_are_exact(
    runtime_fixture: RuntimeFixture,
    case: str,
    field: str,
    value: object,
) -> None:
    del case
    provenance = copy.deepcopy(runtime_fixture.provenance)
    provenance[field] = value
    runtime_fixture.write_provenance(provenance)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


@pytest.mark.parametrize(
    "missing_field",
    [
        "schemaVersion",
        "producerName",
        "distributionName",
        "version",
        "sourceSha256",
        "sourceDateEpoch",
        "requirementsLockSha256",
    ],
)
def test_producer_provenance_rejects_every_missing_field(
    runtime_fixture: RuntimeFixture,
    missing_field: str,
) -> None:
    provenance = copy.deepcopy(runtime_fixture.provenance)
    del provenance[missing_field]
    runtime_fixture.write_provenance(provenance)

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_requirements_lock_is_runtime_bound_through_provenance(
    runtime_fixture: RuntimeFixture,
) -> None:
    locks = runtime_fixture.manifest["locks"]
    assert isinstance(locks, dict)
    locks["requirements"] = "sha256:" + "f" * 64
    runtime_fixture.write_manifest()

    with pytest.raises(RuntimeIdentityError):
        _verify(runtime_fixture)


def test_schema_valid_wheel_attestations_are_seal_owned_and_bytes_are_retained(
    runtime_fixture: RuntimeFixture,
) -> None:
    first = _verify(runtime_fixture)
    package = _package_manifest(runtime_fixture, "numpy")
    package["wheelFilename"] = "numpy-2.2.6-cp312-cp312-macosx_11_0_arm64.whl"
    package["wheelTag"] = "cp312-cp312-macosx_11_0_arm64"
    package["wheelSha256"] = "sha256:" + "a" * 64
    producer = runtime_fixture.manifest["producer"]
    assert isinstance(producer, dict)
    producer["wheelFilename"] = "sfep_equipment_quality-1.0.0-cp312-none-any.whl"
    producer["wheelSha256"] = "sha256:" + "b" * 64
    changed_bytes = runtime_fixture.write_manifest()

    second = _verify(runtime_fixture)

    assert second.manifest_bytes == changed_bytes
    assert second.manifest_bytes != first.manifest_bytes
    assert second.manifest_sha256 != first.manifest_sha256
    assert second.installed_code_trees == first.installed_code_trees


@pytest.mark.parametrize(
    "lock_name",
    ["pyproject", "bootstrap", "buildRequirements", "wheelhouse", "producer"],
)
def test_non_requirements_lock_attestations_are_seal_owned(
    runtime_fixture: RuntimeFixture,
    lock_name: str,
) -> None:
    first = _verify(runtime_fixture)
    locks = runtime_fixture.manifest["locks"]
    assert isinstance(locks, dict)
    locks[lock_name] = "sha256:" + "d" * 64
    changed_bytes = runtime_fixture.write_manifest()

    second = _verify(runtime_fixture)

    assert second.manifest_bytes == changed_bytes
    assert second.manifest_bytes != first.manifest_bytes
    assert second.installed_code_trees == first.installed_code_trees


def test_runtime_environment_adapter_is_frozen_and_tuple_backed(
    runtime_fixture: RuntimeFixture,
) -> None:
    environment = runtime_fixture.environment
    assert isinstance(environment.distributions, tuple)
    with pytest.raises(FrozenInstanceError):
        environment.timezone = "UTC"  # type: ignore[misc]


def test_all_verification_failures_use_the_single_public_exception(
    runtime_fixture: RuntimeFixture,
) -> None:
    runtime_fixture.set_manifest_bytes(b"not json\n")

    with pytest.raises(RuntimeIdentityError) as error:
        _verify(runtime_fixture)
    assert type(error.value) is RuntimeIdentityError
