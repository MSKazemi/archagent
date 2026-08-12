# ArchAgent production deployment

Notes for a small production/pilot deployment of the dependency-free SQLite app.
Paths below assume the repo is installed at `/opt/archagent`; adjust to taste and keep
the systemd units in `deploy/` consistent with your choice.

## 1. Create a service account

The bundled units run as an unprivileged `archagent` user — never as root.

```bash
sudo useradd --system --home-dir /opt/archagent --shell /usr/sbin/nologin archagent
sudo git clone https://github.com/MSKazemi/archagent.git /opt/archagent
sudo chown -R archagent:archagent /opt/archagent
```

## 2. Secrets: what they are and where they live

Nothing secret is committed to this repository, and nothing secret should be committed to
your copy. Every secret is supplied through the environment.

| Secret | Variable | Required when |
|---|---|---|
| API token, admin-equivalent | `ARCHAGENT_TOKEN` | any non-loopback deployment |
| First admin password | `ARCHAGENT_ADMIN_PASSWORD` | seeding the first user account |
| Azure OpenAI key | `AZURE_OPENAI_KEY` | only for `POST /api/dossier/analyze` |
| SMTP password | `SMTP_PASS` | only for pilot-request email alerts |

**Resolution order.** `archagent_server.py` loads `.env` at startup but **never overrides a
variable already present in the real environment** (`core/config.py:load_env_file`). So a
systemd `EnvironmentFile=`, a container environment variable, or an external secret store
always beats the file on disk. That is what lets a checked-out `.env.example` coexist safely
with production secrets injected from elsewhere.

**Rules for a real host**

- `chmod 600 .env`, owned by the service user. It is the highest-value file on the box.
- Never pass secrets as command-line arguments — they are visible in `ps` to every local user.
- Rotate `ARCHAGENT_TOKEN` by editing `.env` and restarting; rotate user passwords and API
  keys from the admin console (Security tab), which does not require a restart.
- Set `ARCHAGENT_LEGACY_TOKEN_ENABLED=0` once real user accounts exist. While it is on,
  `ARCHAGENT_TOKEN` is admin-equivalent and bypasses RBAC entirely.
- Back up `.env` somewhere encrypted and separate from the database backups. The database is
  useless without it if you have configured Azure or SMTP.

**Startup refuses to run on a bad configuration.** On a non-loopback bind the server exits if
`ARCHAGENT_TOKEN` is absent, a known placeholder, or shorter than 32 characters, or if
`ARCHAGENT_ADMIN_PASSWORD` is a common default or shorter than 12 characters. On localhost it
prints a warning instead.

## 3. Configure secrets

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

## 4. First run

```bash
cd /opt/archagent
set -a; . ./.env; set +a
python3 archagent_server.py --host 127.0.0.1 --port 8091
```

Open `http://127.0.0.1:8091/app`. The databases are created empty on first start;
populate them with a refresh (step 6).

## 5. Docker Compose option

```bash
cd /opt/archagent
cp .env.example .env                      # then fill it in, chmod 600
docker compose up --build -d
docker compose ps
```

`docker compose` reads `./.env` automatically for `${...}` substitution, and the bind mount
puts the same file inside the container where the app loads it. The app container is only
reachable through the bundled nginx, which publishes on **`127.0.0.1:80`** — deliberately
not `0.0.0.0` — so nothing is exposed off-box until you put a TLS terminator in front.

Two things to know about this stack:

- **It is HTTP-only.** The token and session cookie would travel in cleartext if you
  published port 80 to the internet. Put TLS in front (section 10) before exposing it.
- **`ARCHAGENT_SECURE_COOKIES` defaults to `0` here, on purpose.** In Docker the app binds
  `0.0.0.0`, so it would otherwise mark session cookies `Secure`; browsers refuse to accept
  `Secure` cookies delivered over plain HTTP, and admin-console login would fail with no
  useful error. Set it to `1` the moment real TLS is in front.

Secrets never enter the image: `.dockerignore` excludes `.env`, and `COPY . /app` therefore
cannot bake it in. On a real host prefer docker secrets or your platform's secret store over
a bind-mounted file.

## 6. Install the systemd service

```bash
sudo cp deploy/archagent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now archagent.service
sudo systemctl status archagent.service
```

The unit runs as `archagent:archagent` with `ProtectSystem=strict`, `ProtectHome`,
`NoNewPrivileges`, and `ReadWritePaths=/opt/archagent`. If you install elsewhere, update
`WorkingDirectory`, `ExecStart`, and `ReadWritePaths` together.

## 7. Install the refresh timer

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

## 8. Install backup + export timers

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

## 9. SQLite maintenance

```bash
python3 ops/maintenance.py            # PRAGMA integrity_check + optimize
python3 ops/maintenance.py --vacuum   # during a maintenance window, after a backup
```

## 10. HTTPS reverse proxy and firewall

Caddy is the least effort — it obtains and renews certificates automatically:

```caddyfile
archagent.example.com {
  reverse_proxy 127.0.0.1:8091
  encode gzip
}
```

**The rules that matter on an internet-facing VM:**

1. **Keep the app bound to `127.0.0.1`** and let the proxy be the only listener on a public
   interface. `--host 0.0.0.0` plus an open firewall port bypasses TLS entirely.
2. **Set `ARCHAGENT_SECURE_COOKIES=1`** once HTTPS is terminating in front. Behind a proxy
   the app sees a loopback bind and would otherwise omit the `Secure` flag, leaving the
   session cookie liable to be sent over a downgraded connection.
3. **Firewall down to what you need.** Only 22 and 443 should be reachable; 8091 must never
   be:

   ```bash
   sudo ufw default deny incoming
   sudo ufw allow OpenSSH
   sudo ufw allow 443/tcp
   sudo ufw enable
   sudo ss -ltnp | grep 8091     # must show 127.0.0.1:8091, never 0.0.0.0:8091
   ```

4. **Never expose the app without authentication.** With no users and no `ARCHAGENT_TOKEN`,
   a deployment falls back to implicit admin — that path is for localhost development only.
5. **Redirect HTTP to HTTPS** rather than serving both; a `Secure` cookie plus an HTTP
   listener is how sessions break confusingly.

Verify from off-box after setup:

```bash
curl -sI https://archagent.example.com/api/health      # expect 200
curl -sS  http://<vm-ip>:8091/api/health               # expect connection refused
```

## 11. Health and smoke checks

```bash
curl http://127.0.0.1:8091/api/health
python3 ops/healthcheck.py
python3 tests/test_smoke.py
python3 tests/test_regression.py
python3 tests/test_italy.py
python3 ops/maintenance.py
```

## 12. Operational boundaries

ArchAgent produces source-linked lead radar, fit scoring, bid-readiness dossiers, profile
exports, and partner verification tracking. Official tender documents and legal/commercial
eligibility still require human review before any bid, buyer contact, or client delivery.
Imported OpenStreetMap listings are unverified public business records, not vetted partners.
