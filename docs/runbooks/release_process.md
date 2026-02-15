# Release Process

## Branching scheme

- dev: active development branch
- main: stable branch for releases
- release/vX.Y.Z: release branch cut from dev

## Release checklist

1) Run local release gate:
   - `pwsh -NoProfile -File .\scripts\prod-gate.ps1`
2) Run reports smoke (included in prod-gate):
   - `pwsh -NoProfile -File .\scripts\smoke-reports-all.ps1`
3) Alembic sanity:
   - `alembic heads`
   - `alembic history`
4) Update CHANGELOG.md with release notes.
5) Tag the release:
   - `git tag vX.Y.Z`
   - `git push origin vX.Y.Z`

## SemVer guidance

- MAJOR: breaking API changes
- MINOR: backwards-compatible features
- PATCH: bug fixes only

## Hotfix process (minimal)

1) Branch from main: `hotfix/vX.Y.Z+1`
2) Apply fix + tests
3) Run prod-gate
4) Tag and merge back to main and dev
