#!/bin/bash

set -euo pipefail

usage() {
  printf '%s\n' \
    "Usage: $0 --conda PATH --package-cache DIR --lock FILE --output PREFIX" >&2
}

die() {
  printf 'Error: %s\n' "$1" >&2
  exit "${2:-1}"
}

usage_error() {
  usage
  exit 64
}

conda_path=
package_cache=
lock_path=
output=
conda_seen=false
package_cache_seen=false
lock_seen=false
output_seen=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --conda)
      [[ $# -ge 2 && "$conda_seen" == false ]] || usage_error
      conda_path=$2
      conda_seen=true
      shift 2
      ;;
    --package-cache)
      [[ $# -ge 2 && "$package_cache_seen" == false ]] || usage_error
      package_cache=$2
      package_cache_seen=true
      shift 2
      ;;
    --lock)
      [[ $# -ge 2 && "$lock_seen" == false ]] || usage_error
      lock_path=$2
      lock_seen=true
      shift 2
      ;;
    --output)
      [[ $# -ge 2 && "$output_seen" == false ]] || usage_error
      output=$2
      output_seen=true
      shift 2
      ;;
    *)
      usage_error
      ;;
  esac
done

[[ "$conda_seen" == true && "$package_cache_seen" == true ]] || usage_error
[[ "$lock_seen" == true && "$output_seen" == true ]] || usage_error
[[ -n "$conda_path" && -n "$package_cache" && -n "$lock_path" && -n "$output" ]] || usage_error
[[ "$output" == /* && "$output" != / ]] || die "--output must be an absolute prefix other than /."

if ! printf '%s' "$output" | /usr/bin/od -An -v -t u1 | /usr/bin/awk '
  {
    for (i = 1; i <= NF; i++) {
      if ($i < 32 || $i == 127) {
        exit 1
      }
    }
  }
'; then
  die "Unsafe --output: control characters are forbidden."
fi
unsafe_component_re='(^|/)(\.|\.\.)(/|$)'
[[ ! "$output" =~ $unsafe_component_re ]] || \
  die "Unsafe --output: . and .. path components are forbidden."
[[ "$output" != */ && "$output" != *//* ]] || \
  die "Unsafe --output: the path must not contain empty components."

probe_code='import encodings, pathlib, platform, ssl, venv; assert platform.python_version() == "3.12.10"; assert pathlib.Path(encodings.__file__).is_file()'

probe_runtime() {
  local prefix=$1
  [[ -x "$prefix/bin/python3.12" ]] || return 1
  "$prefix/bin/python3.12" -I -S -B -c "$probe_code"
}

if [[ -e "$output" || -L "$output" ]]; then
  if probe_runtime "$output"; then
    printf 'Python 3.12.10 runtime already valid at: %s\n' "$output"
    exit 0
  fi
fi

[[ -x "$conda_path" ]] || die "Conda executable is unavailable: $conda_path"
[[ -d "$package_cache" ]] || die "Package cache is unavailable: $package_cache"
[[ -f "$lock_path" ]] || die "Lock file is unavailable: $lock_path"

if ! /usr/bin/od -An -v -t u1 "$lock_path" | /usr/bin/awk '
  {
    for (i = 1; i <= NF; i++) {
      if (($i < 32 && $i != 10) || $i == 127) {
        exit 1
      }
    }
  }
'; then
  die "Lock file contains a forbidden control character."
fi

archives=()
archive_names=()
canonical_lock_entries=(
  '0a0544cf95f64394fe4959286f5c71f5444ad58feb0602e53becb27448d24da6  ca-certificates-2026.7.22-hbd8a1cb_0.conda'
  'c432626b16768b8dab228bfb706f7060c2d462a21c516d240f68f2f902b5a044  packaging-26.3-pyhc364b38_0.conda'
  'c205bae42eb11b364fe3663a817cbcbc20a8ef544c6b2ff6f391013487117259  pip-26.2.1-pyh8b19718_0.conda'
  '9e200ee5f9ff19a4d94e4b51c4856d53dec849f91032f345cf0c6bc3d51a7183  setuptools-84.0.0-pyh332efcf_0.conda'
  'b928c30ddcb0e3f544c6eade8352737e6e610e263276b90232db6a578ef899d8  tzdata-2026c-h151e31d_0.conda'
  'ba64b29b6d418024cb565081a1799262cb2780d2534ff4c14f76aa858c4286cd  wheel-0.48.0-pyhd8ed1ab_0.conda'
  '8ec22f0ba25cbfc2e64d70cf29459eccd7ffdf6436f6a6ff15bbfef799f7d4f6  bzip2-1.0.8-h4e30115_10.conda'
  '6cdb5dee54c72e56ab189fb3ad33cb28533553d42590e7e831160248f4416a43  icu-78.3-py310h579977c_2.conda'
  '5af74261101e3c777399c6294b2b5d290e508153268eb2e9ff99c4d69834612f  libexpat-2.8.1-hf6b4638_1.conda'
  'c6a530924a9b14e193ea9adfe92843de2a806d1b7dbfd341546ece9653129e60  libffi-3.4.6-h1da3d7d_1.conda'
  '23d0630046a3e8b164d8f80f2b74ed2605af2e7050ab9913018056402fae4311  liblzma-5.8.3-h8088a28_1.conda'
  '839b31d4830e896b4d315b551d27f2bb08026bc946df04bcc360d8627c3ba2cd  libsqlite-3.53.4-hca69786_1.conda'
  'a18fa5d5bac452401459f966cf0d872224e8080c4ff93c77e168d43ab42ef9d7  libzlib-1.3.2-h8088a28_3.conda'
  '7024a48c8c0d0114ed4ab53c76bf9275d50e91ba7cea367a9aead638d3c29c68  ncurses-6.6-he64c551_1.conda'
  'f23239eacd75c4c50705e68fae1aa3292da473e6a3a4abe2330f1e6afa680704  openssl-3.6.4-h55eecbc_0.conda'
  '69aed911271e3f698182e9a911250b05bdf691148b670a23e0bea020031e298e  python-3.12.10-hc22306f_0_cpython.conda'
  'd6782b430e6dce4fe8c7ac118cd988d5a193eb4a7f60741edfaede58016b6110  readline-8.3-h8b90a29_1.conda'
  '857d89087ae7f2c328cf256728affcb7343031a148b4af847334d248a9cf564c  tk-8.6.13-hbeba79b_4.conda'
)
line_number=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line_number=$((line_number + 1))
  if [[ ! "$line" =~ ^([0-9a-f]{64})\ \ ([A-Za-z0-9][A-Za-z0-9._+-]*\.conda)$ ]]; then
    die "Malformed lock entry on line $line_number."
  fi

  expected_digest=${BASH_REMATCH[1]}
  archive_name=${BASH_REMATCH[2]}
  for existing_name in "${archive_names[@]-}"; do
    [[ "$existing_name" != "$archive_name" ]] || die "Duplicate lock entry: $archive_name"
  done

  canonical_index=$((line_number - 1))
  if [[ $canonical_index -ge ${#canonical_lock_entries[@]} ]] || \
     [[ "$line" != "${canonical_lock_entries[$canonical_index]}" ]]; then
    die "Lock file does not match the canonical pinned manifest at line $line_number."
  fi

  archive_path="$package_cache/$archive_name"
  [[ -f "$archive_path" ]] || die "Missing archive: $archive_path"
  actual_digest=$(/usr/bin/shasum -a 256 "$archive_path")
  actual_digest=${actual_digest%% *}
  [[ "$actual_digest" == "$expected_digest" ]] || \
    die "SHA-256 mismatch for archive: $archive_path"

  archive_names+=("$archive_name")
  archives+=("$archive_path")
done < "$lock_path"

[[ $line_number -eq ${#canonical_lock_entries[@]} ]] || \
  die "Lock file does not match the canonical pinned manifest: expected ${#canonical_lock_entries[@]} entries, found $line_number."

preserve_prefix() {
  local label=$1
  local prefix=$2
  local recovery_dir
  local recovery_target

  recovery_dir=$(/usr/bin/mktemp -d /private/tmp/sfep-python-damaged.XXXXXX) || \
    die "Could not create a recovery directory for $label prefix."
  recovery_target="$recovery_dir/$(/usr/bin/basename "$prefix")"
  if ! /bin/mv "$prefix" "$recovery_target"; then
    die "Could not preserve $label prefix at $recovery_target."
  fi
  printf '%s Python prefix preserved at: %s\n' "$label" "$recovery_target" >&2
}

if [[ -e "$output" || -L "$output" ]]; then
  preserve_prefix "Damaged" "$output"
fi

conda_pkgs_dir=$(/usr/bin/mktemp -d /private/tmp/sfep-python-conda-pkgs.XXXXXX) || \
  die "Could not create the task-local Conda package cache."
creation_active=true

cleanup() {
  local exit_status=$?
  trap - EXIT HUP INT TERM
  if [[ "${creation_active:-false}" == true && ( -e "$output" || -L "$output" ) ]]; then
    preserve_prefix "Partial" "$output"
  fi
  if [[ -n "${conda_pkgs_dir:-}" && -d "$conda_pkgs_dir" ]]; then
    if ! /bin/rm -rf "$conda_pkgs_dir"; then
      printf 'Warning: could not remove task-local Conda package cache: %s\n' \
        "$conda_pkgs_dir" >&2
    fi
  fi
  exit "$exit_status"
}

handle_signal() {
  local signal_name=$1
  local exit_status=$2
  printf 'Interrupted by signal %s; preserving any partial prefix.\n' \
    "$signal_name" >&2
  exit "$exit_status"
}
trap cleanup EXIT
trap 'handle_signal HUP 129' HUP
trap 'handle_signal INT 130' INT
trap 'handle_signal TERM 143' TERM

set +e
CONDA_REGISTER_ENVS=false \
CONDA_PKGS_DIRS="$conda_pkgs_dir" \
CONDA_OFFLINE=true \
  "$conda_path" create --yes --prefix "$output" \
    --offline --copy --no-default-packages "${archives[@]}"
conda_status=$?
set -e

if [[ $conda_status -ne 0 ]]; then
  die "Conda failed to create the Python prefix (exit $conda_status)."
fi

if ! probe_runtime "$output"; then
  die "Created prefix failed Python 3.12.10/stdlib verification."
fi

creation_active=false
printf 'Restored Python 3.12.10 runtime at: %s\n' "$output"
