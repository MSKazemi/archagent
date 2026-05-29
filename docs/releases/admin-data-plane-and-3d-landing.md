# Release: Admin data plane + 3D landing page (v0.5.0, 2026-05-29)

## Summary
Two user-facing initiatives shipped: a high-end **3D landing page** and an **enterprise admin data
plane** (backup/restore/export/retention). Both are pure Python 3 stdlib (the only client asset is
vendored Three.js; backups/exports use `sqlite3`/`csv`/`json`/`zipfile`).

## Landing page
- Interactive Three.js WebGL hero (wireframe "city" + particle field, mouse-parallax, idle rotation)
  over a dark gradient; Unsplash architectural photography; inline SVG glyphs; adaptive nav; scroll
  reveals. Vendored lib at `frontend/libs/` (no build step), loaded via importmap.
- Graceful degradation: reduced-motion → single static frame; no-WebGL → static hero; pauses
  offscreen / on hidden tab.
- Server: `/`, `/app.html`, `/admin.html` route rewrites; `/frontend/libs/` made public.

## Admin data plane (`archagent/api/handlers/admin_data.py`)
| Capability | Endpoint(s) |
|---|---|
| Backup both DBs (verified, pruned) | `POST /api/admin/security/backups` |
| Download / Verify / Restore / Rescan | `GET .../backups/{id}/download`, `POST .../backups/{id}/{verify,restore}`, `POST .../backups/rescan` |
| Per-table export (CSV/JSON) | `GET /api/admin/export/{db}/{table}?format=` |
| Full-DB zip export | `POST /api/admin/export/all` |
| Download an export | `GET /api/admin/export/download?name=` |
| DB statistics | `GET /api/admin/ops/db-stats` |
| Retention preview / purge | `GET /api/admin/ops/retention`, `POST /api/admin/ops/retention/purge` |

- Restore takes a pre-restore safety backup, replaces the live DB file atomically, drops WAL/SHM
  sidecars, then rescans the backup dir to rebuild the (reverted) index.
- Exports redact secret columns (password hashes) and exclude auth-secret tables.
- Migration **m9** adds `db_name | kind | verified | note` to `backups`.
- Scheduled units: `archagent-backup.timer` (daily 02:00), `archagent-export.timer` (weekly, export-only).

## Verification
All seven test suites pass: `smoke`, `regression`, `italy`, `db`, `admin`, `admin_data`, `procurement`.
End-to-end (authenticated) + Playwright UI verified for backup/download/export/restore.

## Deferred to next session
- Security hardening: rotate the dev `admin/admin` credentials; backup encryption + off-site copy.
- Configure a git remote and push (no remote currently set).
