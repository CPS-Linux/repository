# CPS Linux core recipes

The core repository currently defines these bootstrap packages for x86_64:

| Package | Upstream version | Runtime dependencies | Provides |
| --- | --- | --- | --- |
| `linux-upstream` | 7.1.8 | `kmod` | `kernel`, `linux`, `linux-api-headers` |
| `glibc` | 2.43 | none | `libc` |
| `gcc` | 16.1.0 | `glibc>=2.43`, `binutils` | C/C++ compiler and runtime capabilities |

The source archives use release-specific HTTPS URLs and pinned SHA-256
digests. The GCC recipe additionally runs upstream's
`contrib/download_prerequisites --no-isl --sha512`; that script downloads
GMP, MPFR, MPC, and gettext from gcc.gnu.org and verifies the SHA-512 values
shipped in the GCC release archive. This is a bootstrap workaround until the
recipe format supports multiple independently checksummed sources.

## Bootstrap boundary

`cpsbuild` records and warns about build dependencies but does not resolve or
install them. These recipes therefore assume an existing native x86_64 GNU
toolchain and the tools named in each recipe's `build-depends`. In particular,
GCC needs a working C++ compiler, while glibc and GCC form the usual compiler / C
library bootstrap cycle.

`binutils` and `kmod` are real runtime dependencies but are not yet recipes in
this repository. They must be added or supplied by an already configured CPS
repository before `cpsi` can resolve every package in this set.

The glibc payload establishes the x86_64 merged-`/usr` loader layout:
`/lib64 -> usr/lib64`, with the ABI interpreter linked from `/usr/lib64` to
the real loader in `/usr/lib`. All payload links are relative and remain inside
`data/`, as required by both `cpsbuild verify` and `cpsi`.

Neither recipe uses a host-mutating post-install hook. Linux generates module
dependency metadata inside `PKG_INSTALL_DIR` during the build. A completed
offline image should generate its dynamic-linker cache after all libraries are
installed, for example with `ldconfig -r /path/to/root`.

Do not upgrade glibc on a running root with the current `cpsi`. Its regular-file
copy path overwrites an existing inode in place, which is unsafe for a mapped
`libc.so.6`. Until `cpsi` replaces existing regular files atomically using a
same-directory temporary file plus `rename`, this glibc recipe is supported for
new or offline roots only.

The Linux recipe starts from upstream `x86_64_defconfig` and merges the tracked
CPS fragment. It installs the kernel, modules, and userspace API headers. It
does not generate an initramfs or update a bootloader because CPS Linux has not
yet defined either policy. The checked-in fragment is a bootstrap baseline,
not a hardware-complete production kernel configuration.

## Suggested build order

On a prepared builder, build the recipes individually so failures are easy to
attribute:

```sh
cpsbuild build --output-dir core core/linux-upstream-7.1.8.cpsb
cpsbuild build --output-dir core core/glibc-2.43.cpsb
cpsbuild build --output-dir core core/gcc-16.1.0.cpsb
cpsbuild verify core/linux-upstream-7.1.8-k1-x86_64.clos
cpsbuild verify core/glibc-2.43.0-k1-x86_64.clos
cpsbuild verify core/gcc-16.1.0-k1-x86_64.clos
```

Builds are native. `--target-arch` currently validates the recipe architecture;
it does not create a cross-compilation sysroot or toolchain.

## Automated Linux upstream updates

`.github/workflows/update-linux-upstream.yml` runs every day at 04:17 JST and
can also be started with `workflow_dispatch`. It follows only kernel.org's
non-EOL `latest_stable` release; release candidates and mainline releases are
ignored.

For a new stable version, the workflow checks the tar.xz archive against its
entry in kernel.org's `sha256sums.asc`, renames the recipe, updates all version
references, temporarily generates the upstream x86_64 configuration, merges
the CPS fragment, and checks every requested Kconfig value. A fixed
`automation/update-linux-upstream` branch is then used to create or refresh a
reviewable pull request. Kernel updates are deliberately not auto-merged.
Upstream Kconfig code runs only in a read-only validation job; the separate
write-enabled job receives and applies the validated repository patch without
executing code from the downloaded archive.

Repository administrators must enable **Allow GitHub Actions to create and
approve pull requests** under **Settings → Actions → General → Workflow
permissions**. Organization policy can override this repository setting.
