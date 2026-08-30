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

  archive_path="$package_cache/$archive_name"
  [[ -f "$archive_path" ]] || die "Missing archive: $archive_path"
  actual_digest=$(/usr/bin/shasum -a 256 "$archive_path")
  actual_digest=${actual_digest%% *}
  [[ "$actual_digest" == "$expected_digest" ]] || \
    die "SHA-256 mismatch for archive: $archive_path"

  archive_names+=("$archive_name")
  archives+=("$archive_path")
done < "$lock_path"

[[ $line_number -gt 0 ]] || die "Lock file must contain at least one archive."

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

cleanup() {
  if [[ -n "${conda_pkgs_dir:-}" && -d "$conda_pkgs_dir" ]]; then
    /bin/rm -rf "$conda_pkgs_dir"
  fi
}
trap cleanup EXIT

set +e
CONDA_REGISTER_ENVS=false \
CONDA_PKGS_DIRS="$conda_pkgs_dir" \
CONDA_OFFLINE=true \
  "$conda_path" create --yes --prefix "$output" \
    --offline --copy --no-default-packages "${archives[@]}"
conda_status=$?
set -e

if [[ $conda_status -ne 0 ]]; then
  if [[ -e "$output" || -L "$output" ]]; then
    preserve_prefix "Partial" "$output"
  fi
  die "Conda failed to create the Python prefix (exit $conda_status)."
fi

if ! probe_runtime "$output"; then
  if [[ -e "$output" || -L "$output" ]]; then
    preserve_prefix "Partial" "$output"
  fi
  die "Created prefix failed Python 3.12.10/stdlib verification."
fi

printf 'Restored Python 3.12.10 runtime at: %s\n' "$output"
