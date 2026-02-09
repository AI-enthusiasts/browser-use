# File Reversion Root Cause — PROVEN

## Root Cause Chain

1. **Immediate**: File `nul` in working directory (Windows reserved device name)
2. **Architectural**: `Snapshot.track()` calls `git add .` with `.nothrow()` — fatal error swallowed
3. **Consequence**: `write-tree` returns stale tree hash e68154af (pre-edit versions)
4. **Trigger**: User `/undo` → `Snapshot.revert()` → `git checkout e68154af -- <file>` → 5 files overwritten

## Evidence

- Log: `2026-02-06T175229.log`, lines 20321-20330
- `POST /session/.../revert` at 18:06:54 (explicit /undo)
- All snapshot hashes identical: e68154af (git add never succeeded)
- Reproduced: `git add --dry-run .` → `error: short read while indexing nul` → `fatal`

## Fix Spec

Full spec: `docs/opencode-snapshot-fix-spec.md` (743 lines)
Key fixes:
- track(): check exit code of `git add .`, return undefined on failure
- revert(): pre-condition check, backup before overwrite
- restore(): remove `-f` flag
- Remove `fs.unlink()` from fallback

## Immediate Action

Delete `nul` file from browser-use working directory.
