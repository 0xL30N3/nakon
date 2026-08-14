# Changelog

All notable changes to nakon are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.2] — 2026-08-14

### Fixed (vulndb catalog content, not this package's code)
- `ADDS` (promotes a box to a new AD forest) was missing `-SafeModeAdministratorPassword` —
  `Install-ADDSForest` always interactively prompts for it when omitted, which nakon's
  non-interactive transport could never satisfy. Now takes a `$dsrm_password` var. Also fixed:
  a missing Windows client-side domain-join config (new row, "Domain Join"), and several other
  Windows catalog rows that failed under nakon's non-interactive PowerShell session (missing
  `-NoRestart`, missing registry keys, non-idempotent re-runs) — see tezcatlipoca's session notes
  for the full list; none of these are file changes in this repo.

### Documented
- Windows deploy path (`render_bootstrap_ps1`) and the AD/domain-controller path are both
  verified end-to-end against real boxes, including a full domain-join scenario driven through
  tezcatlipoca. Only the `winget`/`choco` package-manager fallback remains untested.

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
