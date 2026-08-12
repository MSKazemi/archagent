# Security policy

## Supported versions

ArchAgent is early software. Only the latest commit on `main` receives security fixes.

| Version | Supported |
|---|---|
| `main` (latest) | Yes |
| Tagged releases ≤ v0.5.0 | No — upgrade to `main` |

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report privately through
[GitHub Security Advisories](https://github.com/MSKazemi/archAgent/security/advisories/new),
which is the preferred channel. If that is unavailable to you, contact the maintainer through
[mskazemi.github.io](https://mskazemi.github.io).

Please include: what you found, how to reproduce it, the impact you believe it has, and the
commit you tested. A proof of concept helps a great deal.

This is a single-maintainer project, so please be realistic about timing: expect an
acknowledgement within about a week, and a fix or a plan within about 30 days for a confirmed
issue. You will be credited in the advisory and the changelog unless you ask otherwise.

## Deploying ArchAgent safely

The defaults are tuned for local development. Before exposing an instance to any network:

- **Set a real token.** `ARCHAGENT_TOKEN` must be at least 32 random characters. The server
  refuses to bind a non-loopback address with a placeholder or short token.
- **Set a strong admin password.** `ARCHAGENT_ADMIN_PASSWORD` must be 12+ characters and not a
  well-known default; the server refuses to start on a non-loopback bind otherwise.
- **Know the implicit-admin fallback.** With no users and no token configured, a **localhost**
  deployment grants implicit admin so that development works out of the box. Never run that
  configuration on a reachable interface.
- **Disable the legacy token** once user accounts exist: `ARCHAGENT_LEGACY_TOKEN_ENABLED=0`.
  While enabled, `ARCHAGENT_TOKEN` is admin-equivalent and bypasses RBAC.
- **Terminate TLS at a reverse proxy**, keep the app bound to `127.0.0.1`, and set
  `ARCHAGENT_SECURE_COOKIES=1`.
- **Run as an unprivileged user.** The bundled systemd units run as `archagent` with
  `ProtectSystem=strict`; do not run the server as root.
- **Protect `.env`** with `chmod 600`. It holds the token, admin password, and any Azure key.
- **Guard the data.** The databases contain business contact records and, once you log in,
  session material. Keep backups encrypted and off the web root.

## What is in scope

Authentication and session handling, RBAC and route permissions, the rate limiter, path
handling in the static/export/backup file routes, SQL construction, the admin export/restore
plane, and the audit trail.

## What is out of scope

- The implicit-admin fallback on localhost with no users and no token — documented above and
  intentional.
- Findings that require an attacker to already hold admin credentials or filesystem access.
- Correctness of the D.Lgs 36/2023 encoding — that is a **legal accuracy** issue, not a
  security one. Please open a normal issue with the article citation; those are very welcome.
- Rate-limit tuning defaults, denial of service from an authenticated admin, and anything in
  third-party data sources (TED, OpenStreetMap).

## Data protection note

Imported OpenStreetMap records and ingested tender notices can contain personal data of sole
traders and named contacts. If you operate an instance in the EU/EEA you are the controller for
that processing. The admin plane provides GDPR subject export and erasure
(`/api/admin/audit/gdpr/*`) and a retention purge to help you meet those obligations.
