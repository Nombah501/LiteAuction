# Sprint Manifests

Store each sprint plan as a TOML manifest and sync it to GitHub issues/PR scaffolds.

Session bootstrap: read `AGENTS.md` and `planning/STATUS.md` before editing any manifest.

## Quick Start

1. Copy template:

```bash
cp planning/sprints/sprint-template.toml planning/sprints/sprint-<number>.toml
```

2. Edit `planning/sprints/sprint-<number>.toml` and fill items.

3. Sync issues + milestone:

```bash
python scripts/sprint_sync.py --manifest planning/sprints/sprint-<number>.toml
```

4. Also create draft PR scaffolds:

```bash
python scripts/sprint_sync.py --manifest planning/sprints/sprint-<number>.toml --create-draft-prs
```

## Notes

- Every item should have a stable `id` (for example, `S37-001`) to allow idempotent re-sync.
- The sync updates `planning/STATUS.md` by default so context is recoverable after chat/session resets.
- PR policy workflow requires a sprint label and linked issue in PR body (`Closes #<id>`).
