# Changelog

All notable changes to nakon are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.1] — 2026-08-13

### Fixed
- Corrected the setuptools table layout in `pyproject.toml` so editable installs and wheel builds
  work with the dynamically read `__version__`.

## [0.1.0] — 2026-08-13

First versioned release. Resets the version number (the codebase previously carried an un-tagged
`2.0.0` string with no releases or tags); from here on releases are git-tagged `vX.Y.Z`.

### Added
- Non-interactive `nakon randomize` mode: `--platform`/`--os`, `--services`/`--vulns`,
  `--difficulty`, `--exclude`, `--source`, `--json`. Emits `{"platform","services","vulns"}` so
  orchestrators (tezcatlipoca) can pick a selection via the CLI instead of importing
  `nakon.catalog.randomize` internals.
- `AGENTS.md` (agent/integration context) and `CHANGELOG.md`.
- Version is now single-sourced from `nakon/__init__.py:__version__`, read dynamically by
  `pyproject.toml`.

### Changed
- Consumers should integrate via the **CLI** (`python3 -m nakon … --json`), not by importing
  internal modules. The public lazy API (`build`, `deploy`, `summarize`, `Bundle`,
  `load_machines`) remains for embedders.

### Removed
- `randomize_config.py` root compatibility shim (deprecated; no consumer loads it by path anymore).
