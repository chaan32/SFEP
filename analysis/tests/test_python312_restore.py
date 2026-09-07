from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import textwrap
import time

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "restore-sfep-python312.sh"
LOCK = PROJECT_ROOT / "analysis" / "python312-conda.lock"
REAL_PACKAGE_CACHE = Path("/opt/homebrew/Caskroom/miniconda/base/pkgs")

EXPECTED_LOCK = """\
0a0544cf95f64394fe4959286f5c71f5444ad58feb0602e53becb27448d24da6  ca-certificates-2026.7.22-hbd8a1cb_0.conda
c432626b16768b8dab228bfb706f7060c2d462a21c516d240f68f2f902b5a044  packaging-26.3-pyhc364b38_0.conda
c205bae42eb11b364fe3663a817cbcbc20a8ef544c6b2ff6f391013487117259  pip-26.2.1-pyh8b19718_0.conda
9e200ee5f9ff19a4d94e4b51c4856d53dec849f91032f345cf0c6bc3d51a7183  setuptools-84.0.0-pyh332efcf_0.conda
b928c30ddcb0e3f544c6eade8352737e6e610e263276b90232db6a578ef899d8  tzdata-2026c-h151e31d_0.conda
ba64b29b6d418024cb565081a1799262cb2780d2534ff4c14f76aa858c4286cd  wheel-0.48.0-pyhd8ed1ab_0.conda
8ec22f0ba25cbfc2e64d70cf29459eccd7ffdf6436f6a6ff15bbfef799f7d4f6  bzip2-1.0.8-h4e30115_10.conda
6cdb5dee54c72e56ab189fb3ad33cb28533553d42590e7e831160248f4416a43  icu-78.3-py310h579977c_2.conda
5af74261101e3c777399c6294b2b5d290e508153268eb2e9ff99c4d69834612f  libexpat-2.8.1-hf6b4638_1.conda
c6a530924a9b14e193ea9adfe92843de2a806d1b7dbfd341546ece9653129e60  libffi-3.4.6-h1da3d7d_1.conda
23d0630046a3e8b164d8f80f2b74ed2605af2e7050ab9913018056402fae4311  liblzma-5.8.3-h8088a28_1.conda
839b31d4830e896b4d315b551d27f2bb08026bc946df04bcc360d8627c3ba2cd  libsqlite-3.53.4-hca69786_1.conda
a18fa5d5bac452401459f966cf0d872224e8080c4ff93c77e168d43ab42ef9d7  libzlib-1.3.2-h8088a28_3.conda
7024a48c8c0d0114ed4ab53c76bf9275d50e91ba7cea367a9aead638d3c29c68  ncurses-6.6-he64c551_1.conda
f23239eacd75c4c50705e68fae1aa3292da473e6a3a4abe2330f1e6afa680704  openssl-3.6.4-h55eecbc_0.conda
69aed911271e3f698182e9a911250b05bdf691148b670a23e0bea020031e298e  python-3.12.10-hc22306f_0_cpython.conda
d6782b430e6dce4fe8c7ac118cd988d5a193eb4a7f60741edfaede58016b6110  readline-8.3-h8b90a29_1.conda
857d89087ae7f2c328cf256728affcb7343031a148b4af847334d248a9cf564c  tk-8.6.13-hbeba79b_4.conda
"""

PROBE_CODE = (
    "import encodings, pathlib, platform, ssl, venv; "
    'assert platform.python_version() == "3.12.10"; '
    "assert pathlib.Path(encodings.__file__).is_file()"
)


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _write_runtime(prefix: Path, mode: str) -> Path:
    runtime = prefix / "bin" / "python3.12"
    _write_executable(
        runtime,
        textwrap.dedent(
            f"""\
            #!/bin/bash
            set -eu
            if [[ -n "${{FAKE_RUNTIME_LOG:-}}" ]]; then
              printf '%s\\n' "$@" > "$FAKE_RUNTIME_LOG"
            fi
            case {mode!r} in
              valid) exit 0 ;;
              wrong_version)
                printf '%s\\n' 'expected Python 3.12.10, found 3.12.9' >&2
                exit 41
                ;;
              missing_encodings)
                printf '%s\\n' 'ModuleNotFoundError: No module named encodings' >&2
                exit 42
                ;;
            esac
            exit 43
            """
        ),
    )
    return runtime


def _make_case(tmp_path: Path, *, runtime_mode: str = "valid") -> dict[str, Path]:
    package_cache = tmp_path / "package-cache"
    package_cache.mkdir()
    for entry in EXPECTED_LOCK.splitlines():
        _, name = entry.split("  ", maxsplit=1)
        source = REAL_PACKAGE_CACHE / name
        assert source.is_file(), f"required local archive is missing: {source}"
        (package_cache / name).symlink_to(source)

    lock = tmp_path / "python312-conda.lock"
    lock.write_text(EXPECTED_LOCK, encoding="ascii")

    runtime_template = tmp_path / "runtime-template"
    _write_runtime(runtime_template, runtime_mode)
    runtime_template = runtime_template / "bin" / "python3.12"

    conda_log = tmp_path / "conda.log"
    fake_conda = tmp_path / "fake-conda"
    _write_executable(
        fake_conda,
        textwrap.dedent(
            """\
            #!/bin/bash
            set -eu
            : "${FAKE_CONDA_LOG:?}"
            : "${FAKE_RUNTIME_TEMPLATE:?}"
            {
              printf 'REGISTER=%s\\n' "${CONDA_REGISTER_ENVS-}"
              printf 'PKGS=%s\\n' "${CONDA_PKGS_DIRS-}"
              if [[ -d "${CONDA_PKGS_DIRS-}" ]]; then
                printf 'PKGS_IS_DIR=yes\\n'
              else
                printf 'PKGS_IS_DIR=no\\n'
              fi
              if [[ -w "${CONDA_PKGS_DIRS-}" ]]; then
                printf 'PKGS_IS_WRITABLE=yes\\n'
              else
                printf 'PKGS_IS_WRITABLE=no\\n'
              fi
              for arg in "$@"; do
                printf 'ARG=%s\\n' "$arg"
              done
            } > "$FAKE_CONDA_LOG"

            prefix=
            previous=
            for arg in "$@"; do
              if [[ "$previous" == "--prefix" ]]; then
                prefix=$arg
              fi
              previous=$arg
            done
            [[ -n "$prefix" ]] || exit 90

            if [[ "${FAKE_CONDA_LEAVE_PARTIAL:-0}" == "1" ]]; then
              mkdir -p "$prefix"
              printf '%s\\n' partial > "$prefix/partial-marker"
            fi
            if [[ "${FAKE_CONDA_WAIT_FOR_SIGNAL:-0}" == "1" ]]; then
              : "${FAKE_CONDA_SIGNAL_READY:?}"
              printf '%s\\n' ready > "$FAKE_CONDA_SIGNAL_READY"
              trap 'exit 129' HUP
              trap 'exit 130' INT
              trap 'exit 143' TERM
              while :; do
                /bin/sleep 1
              done
            fi
            if [[ "${FAKE_CONDA_EXIT:-0}" != "0" ]]; then
              exit "$FAKE_CONDA_EXIT"
            fi

            mkdir -p "$prefix/bin"
            cp "$FAKE_RUNTIME_TEMPLATE" "$prefix/bin/python3.12"
            chmod 755 "$prefix/bin/python3.12"
            """
        ),
    )
    return {
        "package_cache": package_cache,
        "lock": lock,
        "conda": fake_conda,
        "conda_log": conda_log,
        "runtime_template": runtime_template,
    }


def _restore_argv(case: dict[str, Path], output: Path | str) -> list[str]:
    return [
        str(SCRIPT),
        "--conda",
        str(case["conda"]),
        "--package-cache",
        str(case["package_cache"]),
        "--lock",
        str(case["lock"]),
        "--output",
        str(output),
    ]


def _restore_env(
    case: dict[str, Path],
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "FAKE_CONDA_LOG": str(case["conda_log"]),
            "FAKE_RUNTIME_TEMPLATE": str(case["runtime_template"]),
        }
    )
    if extra_env:
        env.update(extra_env)
    return env


def _run_restore(
    case: dict[str, Path],
    output: Path | str,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    assert SCRIPT.is_file(), "restore script is missing"
    return subprocess.run(
        _restore_argv(case, output),
        check=False,
        capture_output=True,
        text=True,
        env=_restore_env(case, extra_env=extra_env),
    )


def _recovery_targets(stderr: str) -> list[Path]:
    return [Path(value) for value in re.findall(r"preserved at: (.+)", stderr)]


def _remove_fake_recoveries(targets: list[Path]) -> None:
    for target in targets:
        if target.parent.name.startswith("sfep-python-damaged."):
            shutil.rmtree(target.parent, ignore_errors=True)


def test_checked_in_lock_is_the_exact_pinned_archive_set() -> None:
    assert LOCK.is_file(), "pinned lock file is missing"
    assert LOCK.read_text(encoding="ascii") == EXPECTED_LOCK


@pytest.mark.parametrize(
    "manifest_change",
    ["missing", "extra", "substituted", "reordered"],
)
def test_rejects_noncanonical_manifest_before_conda(
    tmp_path: Path, manifest_change: str
) -> None:
    case = _make_case(tmp_path)
    lines = EXPECTED_LOCK.splitlines()
    if manifest_change == "missing":
        del lines[5]
    elif manifest_change == "extra":
        payload = b"unapproved archive\n"
        name = "unapproved-1.0-0.conda"
        (case["package_cache"] / name).write_bytes(payload)
        lines.append(f"{hashlib.sha256(payload).hexdigest()}  {name}")
    elif manifest_change == "substituted":
        payload = b"substituted archive\n"
        name = "substituted-1.0-0.conda"
        (case["package_cache"] / name).write_bytes(payload)
        lines[5] = f"{hashlib.sha256(payload).hexdigest()}  {name}"
    elif manifest_change == "reordered":
        lines[4], lines[5] = lines[5], lines[4]
    case["lock"].write_text("\n".join(lines) + "\n", encoding="ascii")

    result = _run_restore(case, tmp_path / "restored")

    assert result.returncode != 0
    assert "canonical pinned manifest" in result.stderr
    assert not case["conda_log"].exists()


@pytest.mark.parametrize(
    "lock_bytes",
    [
        b"",
        b"0" * 64 + b"\n",
        b"0" * 64 + b"  alpha-1.0-0.conda unexpected\n",
        ((EXPECTED_LOCK.splitlines()[0] + "\n") * 2).encode("ascii"),
        b"0" * 64 + b"\talpha-1.0-0.conda\n",
        b"0" * 64 + b"  alpha-1.0-0.conda\r\n",
        b"0" * 64 + b"  alpha-1.0-0.conda\x00\n",
        b"0" * 64 + b"  ../alpha-1.0-0.conda\n",
    ],
    ids=[
        "empty",
        "missing-name",
        "extra-field",
        "duplicate-entry",
        "tab-control",
        "carriage-return-control",
        "nul-control",
        "non-basename",
    ],
)
def test_rejects_malformed_lock_before_conda_or_output_mutation(
    tmp_path: Path, lock_bytes: bytes
) -> None:
    case = _make_case(tmp_path)
    case["lock"].write_bytes(lock_bytes)
    output = tmp_path / "damaged-output"
    output.mkdir()
    marker = output / "keep-me"
    marker.write_text("original", encoding="utf-8")

    result = _run_restore(case, output)

    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "original"
    assert not case["conda_log"].exists()


def test_rejects_tampered_archive_before_conda_or_output_mutation(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    archive = case["package_cache"] / "ca-certificates-2026.7.22-hbd8a1cb_0.conda"
    archive.unlink()
    archive.write_bytes(b"tampered\n")
    output = tmp_path / "damaged-output"
    output.mkdir()
    marker = output / "keep-me"
    marker.write_text("original", encoding="utf-8")

    result = _run_restore(case, output)

    assert result.returncode != 0
    assert "SHA-256 mismatch" in result.stderr
    assert marker.is_file()
    assert not case["conda_log"].exists()


def test_rejects_missing_archive_before_conda_or_output_mutation(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    (case["package_cache"] / "ca-certificates-2026.7.22-hbd8a1cb_0.conda").unlink()
    output = tmp_path / "damaged-output"
    output.mkdir()
    marker = output / "keep-me"
    marker.write_text("original", encoding="utf-8")

    result = _run_restore(case, output)

    assert result.returncode != 0
    assert "Missing archive" in result.stderr
    assert marker.is_file()
    assert not case["conda_log"].exists()


def test_conda_receives_only_locked_archives_and_required_offline_flags(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    extra = case["package_cache"] / "untrusted-extra-9.9-0.conda"
    extra.write_bytes(b"not allowlisted\n")
    output = tmp_path / "restored"

    result = _run_restore(case, output)

    assert result.returncode == 0, result.stderr
    log_lines = case["conda_log"].read_text(encoding="utf-8").splitlines()
    arguments = [line.removeprefix("ARG=") for line in log_lines if line.startswith("ARG=")]
    assert arguments == [
        "create",
        "--yes",
        "--prefix",
        str(output),
        "--offline",
        "--copy",
        "--no-default-packages",
        *[
            str(case["package_cache"] / entry.split("  ", maxsplit=1)[1])
            for entry in EXPECTED_LOCK.splitlines()
        ],
    ]
    assert str(extra) not in arguments


def test_conda_runs_with_registration_disabled_and_writable_task_cache(tmp_path: Path) -> None:
    case = _make_case(tmp_path)

    result = _run_restore(case, tmp_path / "restored")

    assert result.returncode == 0, result.stderr
    log_lines = case["conda_log"].read_text(encoding="utf-8").splitlines()
    assert "REGISTER=false" in log_lines
    assert "PKGS_IS_DIR=yes" in log_lines
    assert "PKGS_IS_WRITABLE=yes" in log_lines
    pkgs_lines = [line for line in log_lines if line.startswith("PKGS=")]
    assert len(pkgs_lines) == 1
    assert pkgs_lines[0].removeprefix("PKGS=").startswith(
        "/private/tmp/sfep-python-conda-pkgs."
    )


def test_valid_existing_output_exits_without_mutation_or_conda(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    output = tmp_path / "already-valid"
    runtime = _write_runtime(output, "valid")
    marker = output / "keep-me"
    marker.write_text("unchanged", encoding="utf-8")
    runtime_before = runtime.read_bytes()
    runtime_log = tmp_path / "runtime.log"

    result = _run_restore(
        case, output, extra_env={"FAKE_RUNTIME_LOG": str(runtime_log)}
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert runtime.read_bytes() == runtime_before
    assert not case["conda_log"].exists()
    assert runtime_log.read_text(encoding="utf-8").splitlines() == [
        "-I",
        "-S",
        "-B",
        "-c",
        PROBE_CODE,
    ]


def test_damaged_output_is_preserved_then_exact_output_is_created(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    output = tmp_path / "damaged-output"
    _write_runtime(output, "missing_encodings")
    marker = output / "original-marker"
    marker.write_text("original", encoding="utf-8")

    result = _run_restore(case, output)
    recoveries = _recovery_targets(result.stderr)
    try:
        assert result.returncode == 0, result.stderr
        assert len(recoveries) == 1
        assert recoveries[0].parent.parent == Path("/private/tmp")
        assert recoveries[0].parent.name.startswith("sfep-python-damaged.")
        assert (recoveries[0] / "original-marker").read_text(encoding="utf-8") == "original"
        assert not marker.exists()
        assert (output / "bin" / "python3.12").is_file()
    finally:
        _remove_fake_recoveries(recoveries)


def test_conda_failure_preserves_original_and_partial_prefixes(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    output = tmp_path / "damaged-output"
    _write_runtime(output, "missing_encodings")
    (output / "original-marker").write_text("original", encoding="utf-8")

    result = _run_restore(
        case,
        output,
        extra_env={"FAKE_CONDA_EXIT": "23", "FAKE_CONDA_LEAVE_PARTIAL": "1"},
    )
    recoveries = _recovery_targets(result.stderr)
    try:
        assert result.returncode != 0
        assert len(recoveries) == 2
        assert (recoveries[0] / "original-marker").is_file()
        assert (recoveries[1] / "partial-marker").is_file()
        assert not output.exists()
    finally:
        _remove_fake_recoveries(recoveries)


@pytest.mark.parametrize(
    ("signal_number", "signal_name"),
    [
        (signal.SIGHUP, "HUP"),
        (signal.SIGINT, "INT"),
        (signal.SIGTERM, "TERM"),
    ],
)
def test_signal_preserves_and_reports_partial_prefix(
    tmp_path: Path, signal_number: signal.Signals, signal_name: str
) -> None:
    case = _make_case(tmp_path)
    output = tmp_path / "restored"
    ready = tmp_path / "signal-ready"
    env = _restore_env(
        case,
        extra_env={
            "FAKE_CONDA_LEAVE_PARTIAL": "1",
            "FAKE_CONDA_WAIT_FOR_SIGNAL": "1",
            "FAKE_CONDA_SIGNAL_READY": str(ready),
        },
    )
    process = subprocess.Popen(
        _restore_argv(case, output),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    recoveries: list[Path] = []
    try:
        deadline = time.monotonic() + 10
        while not ready.is_file() and process.poll() is None:
            if time.monotonic() >= deadline:
                pytest.fail("fake Conda did not reach its signal wait state")
            time.sleep(0.02)
        assert process.poll() is None

        os.killpg(process.pid, signal_number)
        _, stderr = process.communicate(timeout=10)
        recoveries = _recovery_targets(stderr)

        assert process.returncode == 128 + signal_number
        assert len(recoveries) == 1
        assert recoveries[0].parent.parent == Path("/private/tmp")
        assert (recoveries[0] / "partial-marker").is_file()
        assert not output.exists()
        assert signal_name in stderr
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
        _remove_fake_recoveries(recoveries)


@pytest.mark.parametrize(
    ("runtime_mode", "diagnostic"),
    [
        ("wrong_version", "expected Python 3.12.10"),
        ("missing_encodings", "No module named encodings"),
    ],
)
def test_failed_version_or_stdlib_probe_preserves_created_prefix(
    tmp_path: Path, runtime_mode: str, diagnostic: str
) -> None:
    case = _make_case(tmp_path, runtime_mode=runtime_mode)
    output = tmp_path / "restored"

    result = _run_restore(case, output)
    recoveries = _recovery_targets(result.stderr)
    try:
        assert result.returncode != 0
        assert diagnostic in result.stderr
        assert "failed Python 3.12.10/stdlib verification" in result.stderr
        assert len(recoveries) == 1
        assert (recoveries[0] / "bin" / "python3.12").is_file()
        assert not output.exists()
    finally:
        _remove_fake_recoveries(recoveries)


@pytest.mark.parametrize("missing_flag", ["--conda", "--package-cache", "--lock", "--output"])
def test_each_cli_argument_is_required(tmp_path: Path, missing_flag: str) -> None:
    case = _make_case(tmp_path)
    values = {
        "--conda": case["conda"],
        "--package-cache": case["package_cache"],
        "--lock": case["lock"],
        "--output": tmp_path / "restored",
    }
    argv = [str(SCRIPT)]
    for flag, value in values.items():
        if flag != missing_flag:
            argv.extend([flag, str(value)])
    assert SCRIPT.is_file(), "restore script is missing"

    result = subprocess.run(argv, check=False, capture_output=True, text=True)

    assert result.returncode != 0
    assert "Usage:" in result.stderr
    assert not case["conda_log"].exists()


@pytest.mark.parametrize(
    "unsafe_suffix",
    [
        "/child/.",
        "/child/..",
        "/../child",
        "/child/",
        "/child//nested",
        "/bad\nname",
        "/bad\tname",
        "/bad\rname",
        "/bad\x7fname",
    ],
    ids=[
        "dot-component",
        "dotdot-tail",
        "dotdot-middle",
        "trailing-separator",
        "repeated-separator",
        "newline-control",
        "tab-control",
        "carriage-return-control",
        "delete-control",
    ],
)
def test_rejects_unsafe_output_before_input_validation(
    tmp_path: Path, unsafe_suffix: str
) -> None:
    case = _make_case(tmp_path)
    case["conda"] = tmp_path / "missing-conda"
    output = f"{tmp_path}{unsafe_suffix}"

    result = _run_restore(case, output)

    assert result.returncode != 0
    assert "Unsafe --output" in result.stderr
    assert case["package_cache"].is_dir()
    assert not case["conda_log"].exists()


@pytest.mark.parametrize(
    "extra_argv",
    [["--unknown", "value"], ["positional"], ["--output", "duplicate"]],
    ids=["unknown-option", "positional", "duplicate-option"],
)
def test_cli_rejects_unknown_positional_and_duplicate_arguments(
    tmp_path: Path, extra_argv: list[str]
) -> None:
    case = _make_case(tmp_path)
    argv = [
        str(SCRIPT),
        "--conda",
        str(case["conda"]),
        "--package-cache",
        str(case["package_cache"]),
        "--lock",
        str(case["lock"]),
        "--output",
        str(tmp_path / "restored"),
        *extra_argv,
    ]
    assert SCRIPT.is_file(), "restore script is missing"

    result = subprocess.run(argv, check=False, capture_output=True, text=True)

    assert result.returncode != 0
    assert "Usage:" in result.stderr
    assert not case["conda_log"].exists()
