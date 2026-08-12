# ArchAgent production deployment

Notes for a small production/pilot deployment of the dependency-free SQLite app.
Paths below assume the repo is installed at `/opt/archagent`; adjust to taste and keep
the systemd units in `deploy/` consistent with your choice.

## 1. Create a service account

The bundled units run as an unprivileged `archagent` user — never as root.

```bash
sudo useradd --system --home-dir /opt/archagent --shell /usr/sbin/nologin archagent
sudo git clone https://github.com/MSKazemi/archAgent.git /opt/archagent
sudo chown -R archagent:archagent /opt/archagent
```

## 2. Configure secrets

```bash
cd /opt/archagent
sudo -u archagent cp .gitignore.example .gitignore
sudo -u archagent cp .env.example .env
python3 -c 'import secrets; print("ARCHAGENT_TOKEN=" + secrets.token_urlsafe(48))'
python3 -c 'import secrets; print("ARCHAGENT_ADMIN_PASSWORD=" + secrets.token_urlsafe(24))'
```

Paste both generated values into `.env`, then lock it down:

```bash
sudo chmod 600 /opt/archagent/.env && sudo chown archagent:archagent /opt/archagent/.env
```

The server **refuses to start** on a non-loopback bind if `ARCHAGENT_TOKEN` is a
placeholder or shorter than 32 characters, or if `ARCHAGENT_ADMIN_PASSWORD` is a
well-known default or shorter than 12 characters.

## 3. First run

```bash
cd /opt/archagent
set -a; . ./.env; set +a
python3 archagent_server.py --host 127.0.0.1 --port 8091
```

Open `http://127.0.0.1:8091/app`. The databases are created empty on first start;
populate them with a refresh (step 6).

## 4. Docker Compose option

```bash
cd /opt/archagent
cp .env.example .env
export ARCHAGENT_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
docker compose up --build -d
docker compose ps
```

The container binds `0.0.0.0:8091` internally and exposes `8091:8091`. Databases,
exports, and backups are bind-mounted from the project directory.

## 5. Install the systemd service

```bash
sudo cp deploy/archagent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now archagent.service
sudo systemctl status archagent.service
```

The unit runs as `archagent:archagent` with `ProtectSystem=strict`, `ProtectHome`,
`NoNewPrivileges`, and `ReadWritePaths=/opt/archagent`. If you install elsewhere, update
`WorkingDirectory`, `ExecStart`, and `ReadWritePaths` together.

## 6. Install the refresh timer

The daily timer runs a fast refresh: backup → TED/profile/report refresh → top-5 dossier
generation → regression test → maintenance. The OSM worker refresh is excluded because
Overpass rate-limits; run it manually or on a separate weekly timer.

```bash
sudo cp deploy/archagent-refresh.service deploy/archagent-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now archagent-refresh.timer
systemctl list-timers archagent-refresh.timer
```

Manual refreshes:

```bash
python3 ops/refresh.py --skip-workers --dossiers 5   # fast daily
python3 ops/refresh.py --dossiers 5                  # full weekly, includes OSM
python3 ops/refresh_italy.py --skip-workers          # Italy data only
```

## 7. Install backup + export timers

`archagent-backup.timer` (daily 02:00) snapshots both databases — integrity-verified,
pruned to 30 — plus an `exports/` archive. `archagent-export.timer` (weekly Mon 03:00)
writes a full table-level zip export and **never purges**; retention purge stays manual.

```bash
sudo cp deploy/archagent-backup.service deploy/archagent-backup.timer /etc/systemd/system/
sudo cp deploy/archagent-export.service deploy/archagent-export.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now archagent-backup.timer archagent-export.timer
systemctl list-timers 'archagent-*'
```

Manual:

```bash
python3 ops/backup.py --include-exports --keep 30
python3 ops/export.py
python3 ops/export.py --purge --days 365 --confirm   # destructive, explicit opt-in
```

Backups are written to `/opt/archagent/backups`.

## 8. SQLite maintenance

```bash
python3 ops/maintenance.py            # PRAGMA integrity_check + optimize
python3 ops/maintenance.py --vacuum   # during a maintenance window, after a backup
```

## 9. HTTPS reverse proxy

```caddyfile
archagent.example.com {
  reverse_proxy 127.0.0.1:8091
  encode gzip
}
```

Keep the server bound to `127.0.0.1` and terminate TLS at the proxy. Set
`ARCHAGENT_SECURE_COOKIES=1` once you are behind HTTPS. Never expose the app without
either `ARCHAGENT_TOKEN` or seeded user accounts.

## 10. Health and smoke checks

```bash
curl http://127.0.0.1:8091/api/health
python3 ops/healthcheck.py
python3 tests/test_smoke.py
python3 tests/test_regression.py
python3 tests/test_italy.py
python3 ops/maintenance.py
```

## 11. Operational boundaries

ArchAgent produces source-linked lead radar, fit scoring, bid-readiness dossiers, profile
exports, and partner verification tracking. Official tender documents and legal/commercial
eligibility still require human review before any bid, buyer contact, or client delivery.
Imported OpenStreetMap listings are unverified public business records, not vetted partners.
