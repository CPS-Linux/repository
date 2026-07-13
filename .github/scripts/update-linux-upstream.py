#!/usr/bin/env python3
"""Update the CPS Linux upstream-kernel recipe to kernel.org's latest stable."""

from __future__ import annotations

import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


RELEASES_URL = "https://www.kernel.org/releases.json"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
USER_AGENT = "CPS-Linux-kernel-updater/1.0 (+https://github.com/CPS-Linux/repository)"


def fail(message: str) -> "NoReturn":
    raise SystemExit(f"error: {message}")


def run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=True, text=True, capture_output=True)


def download(url: str, destination: Path) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        fail(f"refusing non-HTTPS download URL: {url}")

    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.unlink(missing_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 6):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=90) as response, temporary.open("wb") as output:
                final_url = urlparse(response.geturl())
                final_host = final_url.hostname or ""
                if final_url.scheme != "https" or not (
                    final_host == "kernel.org" or final_host.endswith(".kernel.org")
                ):
                    fail(f"refusing unexpected download redirect: {response.geturl()}")
                shutil.copyfileobj(response, output, length=1024 * 1024)
            temporary.replace(destination)
            return
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            temporary.unlink(missing_ok=True)
            if attempt == 5:
                fail(f"download failed after {attempt} attempts: {url}: {error}")
            time.sleep(2 ** (attempt - 1))


def obtain_file(override_variable: str, url: str, destination: Path) -> Path:
    override = os.environ.get(override_variable)
    if override:
        source = Path(override).resolve()
        if not source.is_file():
            fail(f"{override_variable} is not a file: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    else:
        download(url, destination)
    return destination


def read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"invalid releases JSON: {error}")
    if not isinstance(value, dict):
        fail("kernel.org releases document is not a JSON object")
    return value


def parse_version(version: str) -> tuple[int, int, int]:
    if not VERSION_RE.fullmatch(version):
        fail(f"unsupported stable version: {version!r}")
    parts = [int(part) for part in version.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))  # type: ignore[return-value]


def normalized_version(version: str) -> str:
    return version if version.count(".") == 2 else f"{version}.0"


def expected_source_url(version: str) -> str:
    major = version.split(".", 1)[0]
    return f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/linux-{version}.tar.xz"


def official_checksum(manifest: str, filename: str) -> str:
    pattern = re.compile(
        rf"^([0-9a-fA-F]{{64}})[ \t]+{re.escape(filename)}$", re.MULTILINE
    )
    matches = pattern.findall(manifest)
    if len(matches) != 1:
        fail(f"expected one checksum entry for {filename}, found {len(matches)}")
    checksum = matches[0].lower()
    if not SHA256_RE.fullmatch(checksum):
        fail(f"invalid official checksum for {filename}")
    return checksum


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def replace_once(path: Path, old: str, new: str) -> None:
    contents = path.read_text(encoding="utf-8")
    count = contents.count(old)
    if count != 1:
        fail(f"expected exactly one occurrence in {path}: {old!r}; found {count}")
    path.write_text(contents.replace(old, new, 1), encoding="utf-8")


def write_outputs(**values: object) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            text = str(value).lower() if isinstance(value, bool) else str(value)
            if "\n" in text or "\r" in text:
                fail(f"workflow output {key} contains a newline")
            output.write(f"{key}={text}\n")


def load_current_recipe(repo_root: Path) -> tuple[Path, dict[str, object]]:
    recipes = sorted((repo_root / "core").glob("linux-upstream-*.cpsb"))
    recipes = [path for path in recipes if path.is_dir()]
    if len(recipes) != 1:
        fail(f"expected exactly one linux-upstream recipe, found {len(recipes)}")
    recipe_path = recipes[0] / "recipe.toml"
    try:
        recipe = tomllib.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        fail(f"cannot parse {recipe_path}: {error}")
    return recipes[0], recipe


def main() -> None:
    repo_root = Path(run("git", "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    os.chdir(repo_root)
    recipe_dir, recipe = load_current_recipe(repo_root)

    package = recipe.get("package")
    source = recipe.get("source")
    if not isinstance(package, dict) or not isinstance(source, dict):
        fail("recipe is missing package or source metadata")
    if package.get("name") != "linux-upstream":
        fail("recipe package.name is not linux-upstream")
    if package.get("arch") != ["x86_64"]:
        fail("automated updates currently require arch = [\"x86_64\"]")
    current_version = package.get("version")
    current_release = package.get("release")
    current_url = source.get("url")
    current_sha256 = source.get("sha256")
    if not isinstance(current_version, str):
        fail("package.version is not a string")
    parse_version(current_version)
    if not isinstance(current_release, int) or current_release < 1:
        fail("package.release must be a positive integer")
    if not isinstance(current_url, str):
        fail("source.url is not a string")
    if not isinstance(current_sha256, str) or not SHA256_RE.fullmatch(
        current_sha256.lower()
    ):
        fail("source.sha256 is not a lowercase 64-character digest")
    if recipe_dir.name != f"linux-upstream-{current_version}.cpsb":
        fail("recipe directory and package.version disagree")
    if current_url != expected_source_url(current_version):
        fail("current recipe source URL is not the canonical kernel.org URL")

    work_dir = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir())) / (
        "cps-linux-upstream-update"
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    releases_path = obtain_file(
        "KERNEL_RELEASES_FILE", RELEASES_URL, work_dir / "releases.json"
    )
    releases_document = read_json(releases_path)

    latest_stable = releases_document.get("latest_stable")
    releases = releases_document.get("releases")
    if not isinstance(latest_stable, dict) or not isinstance(releases, list):
        fail("kernel.org JSON is missing latest_stable or releases")
    latest_version = latest_stable.get("version")
    if not isinstance(latest_version, str):
        fail("latest_stable.version is not a string")
    parse_version(latest_version)

    candidates = [
        entry
        for entry in releases
        if isinstance(entry, dict)
        and entry.get("moniker") == "stable"
        and entry.get("version") == latest_version
        and entry.get("iseol") is False
    ]
    if len(candidates) != 1:
        fail(f"expected one non-EOL stable entry for {latest_version}")
    latest_source = candidates[0].get("source")
    if latest_source != expected_source_url(latest_version):
        fail(f"unexpected source URL for stable {latest_version}: {latest_source!r}")

    major = latest_version.split(".", 1)[0]
    filename = f"linux-{latest_version}.tar.xz"
    manifest_url = f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/sha256sums.asc"
    manifest_path = obtain_file(
        "KERNEL_CHECKSUMS_FILE", manifest_url, work_dir / "sha256sums.asc"
    )
    manifest = manifest_path.read_text(encoding="utf-8")
    checksum = official_checksum(manifest, filename)

    if latest_version == current_version:
        if current_url != latest_source or current_sha256.lower() != checksum:
            fail(
                "current stable version was re-rolled or its metadata changed; "
                "refusing an automatic same-version rewrite"
            )
        print(f"linux-upstream is current at {current_version}")
        write_outputs(
            updated=False,
            old_version=current_version,
            new_version=current_version,
            normalized_version=normalized_version(current_version),
            recipe_path=recipe_dir.relative_to(repo_root),
            archive_path="",
        )
        return

    if parse_version(latest_version) <= parse_version(current_version):
        fail(f"refusing stable downgrade from {current_version} to {latest_version}")

    archive_path = work_dir / filename
    archive_override = os.environ.get("KERNEL_ARCHIVE_FILE")
    if archive_override:
        override_path = Path(archive_override).resolve()
        if not override_path.is_file():
            fail(f"KERNEL_ARCHIVE_FILE is not a file: {override_path}")
        shutil.copyfile(override_path, archive_path)
    else:
        download(latest_source, archive_path)

    actual_checksum = sha256_file(archive_path)
    if actual_checksum != checksum:
        fail(
            f"checksum mismatch for {filename}: expected {checksum}, got {actual_checksum}"
        )
    try:
        with lzma.open(archive_path, "rb") as archive:
            while archive.read(1024 * 1024):
                pass
    except (OSError, EOFError, lzma.LZMAError) as error:
        fail(f"invalid xz archive {filename}: {error}")

    recipe_path = recipe_dir / "recipe.toml"
    replace_once(
        recipe_path,
        f'version = "{current_version}"',
        f'version = "{latest_version}"',
    )
    replace_once(recipe_path, f"release = {current_release}", "release = 1")
    replace_once(recipe_path, f'url = "{current_url}"', f'url = "{latest_source}"')
    replace_once(
        recipe_path,
        f'sha256 = "{current_sha256}"',
        f'sha256 = "{checksum}"',
    )
    replace_once(
        recipe_path,
        f'if [ "$kernel_release" != "{current_version}-cps" ]; then',
        f'if [ "$kernel_release" != "{latest_version}-cps" ]; then',
    )

    new_recipe_dir = repo_root / "core" / f"linux-upstream-{latest_version}.cpsb"
    if new_recipe_dir.exists():
        fail(f"destination recipe already exists: {new_recipe_dir}")
    subprocess.run(
        ["git", "mv", "--", str(recipe_dir), str(new_recipe_dir)], check=True
    )

    readme = repo_root / "core" / "README.md"
    replace_once(
        readme,
        f"| `linux-upstream` | {current_version} |",
        f"| `linux-upstream` | {latest_version} |",
    )
    replace_once(
        readme,
        f"core/linux-upstream-{current_version}.cpsb",
        f"core/linux-upstream-{latest_version}.cpsb",
    )
    replace_once(
        readme,
        f"core/linux-upstream-{normalized_version(current_version)}-k1-x86_64.clos",
        f"core/linux-upstream-{normalized_version(latest_version)}-k1-x86_64.clos",
    )

    rewritten_dir, rewritten = load_current_recipe(repo_root)
    rewritten_package = rewritten.get("package")
    rewritten_source = rewritten.get("source")
    if rewritten_dir != new_recipe_dir:
        fail("rewritten recipe directory was not detected")
    if not isinstance(rewritten_package, dict) or not isinstance(rewritten_source, dict):
        fail("rewritten recipe is malformed")
    if (
        rewritten_package.get("version") != latest_version
        or rewritten_package.get("release") != 1
        or rewritten_source.get("url") != latest_source
        or rewritten_source.get("sha256") != checksum
    ):
        fail("rewritten recipe metadata is inconsistent")

    relative_recipe = new_recipe_dir.relative_to(repo_root)
    print(f"updated linux-upstream from {current_version} to {latest_version}")
    write_outputs(
        updated=True,
        old_version=current_version,
        new_version=latest_version,
        normalized_version=normalized_version(latest_version),
        recipe_path=relative_recipe,
        archive_path=archive_path,
    )


if __name__ == "__main__":
    main()
