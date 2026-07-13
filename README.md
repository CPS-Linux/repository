# repository
Official packages repository for CPS Linux

This tree contains `cpsbuild` recipes grouped by support policy:

- `core`: bootstrap, toolchain, kernel, and base-system packages
- `extra`: supported optional packages
- `experimental`: packages whose API or packaging policy is not stable

All current core recipes target `x86_64`. `cpsbuild` reports
`build-depends`, but does not install them; prepare the build host before
building a recipe.

```sh
make core-r
make verify
make index
```

Set `CPSBUILD` when the executable is not on `PATH`, for example:

```sh
make CPSBUILD=/home/konoha/develop/cpsbuild/target/release/cpsbuild core-r
```

See [`core/README.md`](core/README.md) for bootstrap order and package-specific
constraints.
