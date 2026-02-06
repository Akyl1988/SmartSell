# Branching Policy

- All feature/fix work targets dev via pull request; no direct pushes to dev.
- main is protected and only receives release PRs from dev (or a tagged release PR).
- Release PRs into main require passing CI, at least one approval, and a green merge check.
- Use a linear history on main (squash or rebase merges) to keep releases traceable.
- Hotfixes go to dev first, then flow to main via a release PR.
- Branch names: feature/*, fix/*, chore/*, release/* (short and descriptive).
- Delete merged branches to reduce clutter and avoid reusing old branches.
