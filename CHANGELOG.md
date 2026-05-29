# Changelog

All notable changes to ArchAgent / BuildingOS. Pure Python 3 stdlib; zero runtime deps.

## [0.5.0] — 2026-05-29

### Added
- **3D landing page** (`frontend/index.html`): interactive Three.js WebGL hero (vendored at
  `frontend/libs/`, no build step), architectural photography (Unsplash), inline SVG glyphs,
  adaptive dark→light nav, reduced-motion scroll-reveal. Degrades gracefully (no-WebGL / reduced
  motion / offscreen pause).
- **Enterprise admin data plane** (`archagent/api/handlers/admin_data.py`):
  - Backups of **both** databases (app + leads) via the SQLite online backup API, integrity-verified
    (`PRAGMA integrity_check`), one `backups` row per DB, pruned to keep N.
  - Backup **download**, **verify**, and **restore** (atomic file replace + automatic pre-restore
    safety backup + post-restore index rescan).
  - **Data export**: per-table CSV/JSON and full-database zip (CSV + `manifest.json`); secret columns
    redacted, auth-secret tables excluded.
  - **Retention** preview/purge (soft-deleted rows + old `activities`/`error_log`/`login_attempts`).
  - **DB statistics** (per-table row/soft-delete counts, file + WAL sizes).
- Admin console: **Export** tab (6 tabs total), backup download/verify/restore, feature-flag editor,
  Overview record-volume + backup cards.
- `ops/export.py` CLI and `deploy/archagent-backup.{service,timer}` + `deploy/archagent-export.{service,timer}`.
- Migration **m9** — `backups` table gains `db_name | kind | verified | note`.
- **Phase 2 — Italian procurement intelligence** (`archagent/intelligence/procurement.py`): SOA catalog,
  CIG validation, cauzioni, document waterfall, D.Lgs 36/2023 checklist, PNRR tagger, go/no-go scorer,
  `POST /api/procurement/analyze-text`, `GET /api/soa-categories` (36 tests).

### Fixed
- Backup filenames used second-precision timestamps → same-second collisions silently overwrote a
  prior backup (broke restore). Now microsecond + random suffix.
- `/`, `/app.html`, `/admin.html` 404'd after the repo-root → `frontend/` reorg; added route rewrites.
- `/frontend/libs/` is now public so the vendored 3D lib loads when token auth is enabled.
- `tests/test_smoke.py` now authenticates via the legacy token, so it passes regardless of seeded users.

### Changed
- Repository reorganized into the `archagent/` package + `frontend/`, `tests/`, `ops/`, `docs/`,
  `deploy/` (this layout was previously untracked).

### Security
- Deferred (next session): replace the dev `admin/admin` credentials, add backup encryption + off-site
  copy. See ROADMAP "Next".
