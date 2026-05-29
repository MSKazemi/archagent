# ArchAgent production deployment notes

These notes are for a small production/pilot deployment of the current dependency-free SQLite app.

## 1. Configure secrets

Copy the example env file and replace the token with a long random value:

```bash
cd /opt/archagent
cp .env.example .env
python3 - <<'PY'
import secrets
print('ARCHAGENT_TOKEN=' + secrets.token_urlsafe(48))
PY
```

Paste the generated token into `.env`.

## 2. Run locally

```bash
cd /opt/archagent
set -a; . ./.env; set +a
python3 archagent_server.py --host 127.0.0.1 --port 8091
```

Open:

```text
http://127.0.0.1:8091/app
```

The browser app will ask for the API token and stores it in localStorage.

## 3. Docker Compose option

```bash
cd /opt/archagent
cp .env.example .env
# edit .env and set ARCHAGENT_TOKEN, or export ARCHAGENT_TOKEN in the shell
export ARCHAGENT_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
docker compose up --build -d
docker compose ps
```

The container binds `0.0.0.0:8091` internally and exposes `8091:8091`. SQLite databases, exports, and backups are bind-mounted from the project directory.

## 4. Install systemd service

```bash
sudo cp deploy/archagent.service /etc/systemd/system/archagent.service
sudo systemctl daemon-reload
sudo systemctl enable --now archagent.service
sudo systemctl status archagent.service
```

## 5. Install daily Italy refresh timer

Daily timer uses a fast refresh: backup, TED/profile/report refresh, top-5 dossier generation, and regression test. Weekly worker/OSM refresh should be run manually or via a second timer because Overpass can rate-limit.

```bash
sudo cp deploy/archagent-refresh.service /etc/systemd/system/archagent-refresh.service
sudo cp deploy/archagent-refresh.timer /etc/systemd/system/archagent-refresh.timer
sudo systemctl daemon-reload
sudo systemctl enable --now archagent-refresh.timer
systemctl list-timers archagent-refresh.timer
```

## 6. Install backup + export timers

`archagent-backup.timer` (daily 02:00) snapshots both databases (integrity-verified, pruned to 30)
plus an `exports/` archive. `archagent-export.timer` (weekly Mon 03:00) writes a full table-level
zip export; it never purges (retention purge is manual: `python3 ops/export.py --purge --days N --confirm`).

```bash
sudo cp deploy/archagent-backup.service deploy/archagent-backup.timer /etc/systemd/system/
sudo cp deploy/archagent-export.service deploy/archagent-export.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now archagent-backup.timer archagent-export.timer
systemctl list-timers 'archagent-*'
```

Manual refreshes:

```bash
python3 archagent_production_refresh.py --skip-workers --dossiers 5
python3 archagent_production_refresh.py --dossiers 5   # includes OSM worker refresh
```

## 6. Backups

Manual backup:

```bash
python3 archagent_backup.py --include-exports --keep 30
```

Backups are written to:

```text
/opt/archagent/backups
```

## 7. SQLite maintenance

```bash
python3 archagent_maintenance.py
python3 archagent_maintenance.py --vacuum
```

The first command runs `PRAGMA integrity_check` and `PRAGMA optimize`. Use `--vacuum` during maintenance windows after backing up.

## 8. HTTPS reverse proxy

Example Caddy config:

```caddyfile
archagent.example.com {
  reverse_proxy 127.0.0.1:8091
  encode gzip
}
```

Keep `ARCHAGENT_TOKEN` enabled. Do not expose the app publicly without token auth.

## 9. Health and smoke checks

```bash
curl http://127.0.0.1:8091/api/health
python3 smoke_test.py
python3 production_regression_test.py
python3 production_italy_test.py
python3 archagent_maintenance.py
python3 healthcheck.py
```

## 10. Current production boundaries

ArchAgent now produces source-linked Italy lead radar, fit scoring, bid-readiness dossiers, profile exports, and partner verification tracking. Official tender PDFs and legal/commercial eligibility still require human review before any bid, buyer contact, or customer delivery.
