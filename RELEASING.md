# Releasing a new version

1. Bump `__version__` in `mttk/__init__.py` (e.g. `1.1.0`) and commit to `main`.
2. Tag and push:
   ```bash
   git tag v1.1.0
   git push origin v1.1.0
   ```
3. GitHub Actions builds `MoreThanTinyTask.exe`, runs the tests and publishes a
   Release with the exe attached (Actions tab → *Build & Release*).
   Release notes are generated from the commits since the last tag.

The download link `releases/latest/download/MoreThanTinyTask.exe` in the README
always points at the newest release.
