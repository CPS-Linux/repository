#!/usr/bin/env bash
set -Eeuo pipefail

die() {
    echo "error: $*" >&2
    exit 1
}

[[ $# -eq 2 ]] || die "usage: $0 <recipe-dir> <source-archive>"

repo_root="$(git rev-parse --show-toplevel)"
recipe_dir="$(realpath --canonicalize-existing "$1")"
archive="$(realpath --canonicalize-existing "$2")"
recipe="$recipe_dir/recipe.toml"
fragment="$recipe_dir/files/cps-x86_64.config"
[[ -f "$recipe" ]] || die "missing recipe.toml"
[[ -f "$fragment" ]] || die "missing CPS Kconfig fragment"

metadata="$(python3 - "$recipe" <<'PY'
from pathlib import Path
import sys
import tomllib

recipe = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
package = recipe["package"]
source = recipe["source"]
print(
    package["version"],
    package["release"],
    source["url"],
    source["sha256"],
    sep="\t",
)
PY
)"
IFS=$'\t' read -r version release source_url expected_sha256 <<<"$metadata"

[[ "$version" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || die "invalid recipe version"
[[ "$release" == "1" ]] || die "upstream version updates must reset release to 1"
major="${version%%.*}"
expected_url="https://cdn.kernel.org/pub/linux/kernel/v${major}.x/linux-${version}.tar.xz"
[[ "$source_url" == "$expected_url" ]] || die "non-canonical recipe source URL"
actual_sha256="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$actual_sha256" == "$expected_sha256" ]] || die "recipe checksum and archive disagree"

for phase in prepare build install post; do
    awk -v key="$phase" '
        $0 == key " = \"\"\"" { inside = 1; next }
        inside && $0 == "\"\"\"" { exit }
        inside { print }
    ' "$recipe" | /bin/sh -n
done

cd "$repo_root"
expected_worktree_hash="$(git diff --binary HEAD | sha256sum | awk '{print $1}')"

temporary_parent="${RUNNER_TEMP:-/tmp}"
mkdir -p "$temporary_parent"
temporary="$(mktemp -d "$temporary_parent/cps-linux-validation-XXXXXX")"
trap 'rm -rf "$temporary"' EXIT
source_parent="$temporary/source"
build_dir="$temporary/build"
mkdir -p "$source_parent" "$build_dir"
tar --extract --file "$archive" --directory "$source_parent" --no-same-owner
source_dir="$source_parent/linux-$version"
[[ -d "$source_dir" ]] || die "archive top-level directory is not linux-$version"

make --silent --no-print-directory -C "$source_dir" \
    O="$build_dir" \
    ARCH=x86_64 \
    x86_64_defconfig
cp "$build_dir/.config" "$temporary/upstream-defconfig"

(
    cd "$source_dir"
    scripts/kconfig/merge_config.sh \
        -m \
        -O "$build_dir" \
        "$build_dir/.config" \
        "$fragment"
)
make --silent --no-print-directory -C "$source_dir" \
    O="$build_dir" \
    ARCH=x86_64 \
    olddefconfig

# olddefconfig updates .config but may leave auto.conf from defconfig in place.
# kernelrelease reads CONFIG_LOCALVERSION from auto.conf, so synchronize it
# without compiling the kernel or host tools.
make --silent --no-print-directory -C "$source_dir" \
    O="$build_dir" \
    ARCH=x86_64 \
    include/config/auto.conf

declare -A seen=()
while IFS= read -r line || [[ -n "$line" ]]; do
    key=""
    if [[ "$line" =~ ^(CONFIG_[A-Z0-9_]+)= ]]; then
        key="${BASH_REMATCH[1]}"
    elif [[ "$line" =~ ^#\ (CONFIG_[A-Z0-9_]+)\ is\ not\ set$ ]]; then
        key="${BASH_REMATCH[1]}"
    fi
    [[ -n "$key" ]] || continue
    [[ -z "${seen[$key]+present}" ]] || die "duplicate fragment symbol: $key"
    seen[$key]=1
    grep --fixed-strings --line-regexp --quiet -- "$line" "$build_dir/.config" \
        || die "Kconfig did not preserve requested value: $line"
done < "$fragment"

kernel_release="$(
    make --silent --no-print-directory -C "$source_dir" \
        O="$build_dir" \
        ARCH=x86_64 \
        kernelrelease
)"
[[ "$kernel_release" == "$version-cps" ]] \
    || die "unexpected kernelrelease: $kernel_release"

cd "$repo_root"
actual_worktree_hash="$(git diff --binary HEAD | sha256sum | awk '{print $1}')"
[[ "$actual_worktree_hash" == "$expected_worktree_hash" ]] \
    || die "upstream build logic modified the repository worktree"
git diff --check HEAD
mapfile -t changed_paths < <(git diff --name-only HEAD)
[[ ${#changed_paths[@]} -gt 0 ]] || die "updated workflow produced no repository changes"
for path in "${changed_paths[@]}"; do
    case "$path" in
        core/README.md|core/linux-upstream-*.cpsb/*) ;;
        *) die "unexpected changed path: $path" ;;
    esac
done

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
        echo "### Linux upstream recipe validation"
        echo
        echo "- Version: \`$version\`"
        echo "- SHA-256: \`$actual_sha256\`"
        echo "- Kernel release: \`$kernel_release\`"
        echo "- Kconfig fragment entries checked: \`${#seen[@]}\`"
    } >> "$GITHUB_STEP_SUMMARY"
fi

echo "validated linux-upstream $version ($kernel_release)"
