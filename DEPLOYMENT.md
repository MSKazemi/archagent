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
python3 archagent_server.py --port 8091
```

Open:

```text
http://127.0.0.1:8091/app
```

The browser app will ask for the API token and stores it in localStorage.

## 3. Install systemd service

```bash
sudo cp deploy/archagent.service /etc/systemd/system/archagent.service
sudo systemctl daemon-reload
sudo systemctl enable --now archagent.service
sudo systemctl status archagent.service
```

## 4. Install daily Italy refresh timer

Daily timer uses a fast refresh: backup, TED/profile/report refresh, top-5 dossier generation, and regression test. Weekly worker/OSM refresh should be run manually or via a second timer because Overpass can rate-limit.

```bash
sudo cp deploy/archagent-refresh.service /etc/systemd/system/archagent-refresh.service
sudo cp deploy/archagent-refresh.timer /etc/systemd/system/archagent-refresh.timer
sudo systemctl daemon-reload
sudo systemctl enable --now archagent-refresh.timer
systemctl list-timers archagent-refresh.timer
```

Manual refreshes:

```bash
python3 archagent_production_refresh.py --skip-workers --dossiers 5
python3 archagent_production_refresh.py --dossiers 5   # includes OSM worker refresh
```

## 5. Backups

Manual backup:

```bash
python3 archagent_backup.py --include-exports --keep 30
```

Backups are written to:

```text
/opt/archagent/backups
```

## 6. HTTPS reverse proxy

Example Caddy config:

```caddyfile
archagent.example.com {
  reverse_proxy 127.0.0.1:8091
  encode gzip
}
```

Keep `ARCHAGENT_TOKEN` enabled. Do not expose the app publicly without token auth.

## 7. Health and smoke checks

```bash
curl http://127.0.0.1:8091/api/health
python3 smoke_test.py
python3 production_regression_test.py
python3 production_italy_test.py
```

## 8. Current production boundaries

ArchAgent now produces source-linked Italy lead radar, fit scoring, bid-readiness dossiers, profile exports, and partner verification tracking. Official tender PDFs and legal/commercial eligibility still require human review before any bid, buyer contact, or customer delivery.
