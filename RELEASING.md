# Releasing a new version

1. Bump `__version__` in `mttk/__init__.py` (e.g. `0.0.1` → `0.0.2`).
2. Commit and push to `main`.

GitHub Actions (*Build & Release*) runs the tests, builds `MoreThanTinyTask.exe`
and, because no release `v0.0.2` exists yet, publishes one with the exe and its
SHA-256 checksum attached. Release notes are generated from the commits since
the previous release. Pushes that don't change the version just build.

(Pushing a tag like `v0.0.2` also publishes a release.)

The README's download link, `releases/latest/download/MoreThanTinyTask.exe`,
always points at the newest release.
