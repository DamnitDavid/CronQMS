# CronQMS

CronQMS is a **Quality Event Management System (QMS)** for manufacturing SMBs. It
tracks the quality lifecycle end to end — events, investigations, corrective and
preventive actions, controlled documents, audits, training, and change control —
with role-based access control and an append-only audit trail.

Built with FastAPI, SQLAlchemy, Jinja2 + htmx (server-rendered UI), and
PostgreSQL, with schema managed by Alembic.

## Features

- **Defects** — capture, investigate, and close quality defects with a governed
  status workflow.
- **CAPA** — corrective/preventive actions with verification.
- **Documents** — document control with review, approval, and obsolescence.
- **Audits** — plan and conduct audits with checklists and findings.
- **Training** — assign courses, track records, and certify employees.
- **Change Control** — change requests with impact assessment and approval.
- **Alerts** — an inbox for acknowledgements and sign-offs.
- **Reports** — dashboards, overdue-by-owner, Pareto, CSV export.
- **RBAC** — per-organization roles resolved to fine-grained permissions.
- **Audit trail** — append-only history of mutations, attributed to the actor.

## Architecture

```
app/
  api/routes/   HTTP endpoints (JSON APIs + server-rendered pages)
  core/         auth, security, permissions, storage, audit, rate limiting
  models/       SQLAlchemy ORM models
  schemas/      Pydantic request/response models
  services/     domain workflows (events, documents, training, RBAC, …)
  templates/    Jinja2 + htmx UI
  static/       CSS/JS assets
migrations/     Alembic migrations (schema is owned by migrations)
tests/          unittest suite
```

Multi-tenancy is by `organization_id`: users belong to an organization and all
data access is scoped to it.

## Getting started

### Option A — Docker Compose (local development only)

```bash
cp .env.example .env          # then edit values (see Configuration)
docker compose up --build
```

This starts PostgreSQL, runs `alembic upgrade head`, and serves the app at
http://localhost:8000. On first run, open the app and you'll be sent to the
`/setup` wizard to create the first organization and admin.

> `docker-compose.yml` is **development only** (auto-reload, source bind-mount,
> weak default credentials, `DEBUG=true`). For production, see
> [Deploying to production](#deploying-to-production-paas) — do not expose the
> compose stack publicly.

### Option B — Local Python

Requires Python 3.11+ and a running PostgreSQL (or SQLite for a quick spin).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DATABASE_URL and secrets
alembic upgrade head          # apply migrations
uvicorn app.main:app --reload
```

## Configuration

All settings come from environment variables (or a local `.env`). See
[`.env.example`](.env.example) for the full list. Key ones:

| Variable | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `development` \| `staging` \| `production` |
| `DEBUG` | `false` | Enable auto-reload / verbose behavior in dev only |
| `DATABASE_URL` | local Postgres | SQLAlchemy connection string |
| `SECRET_KEY` | placeholder | **Must** be set to a strong value in prod |
| `JWT_SECRET` | placeholder | **Must** be set to a strong value in prod |
| `JWT_EXPIRATION_HOURS` | `24` | Access-token lifetime |
| `SESSION_TIMEOUT_MINUTES` | `15` | Idle auto-logout window |
| `PASSWORD_MIN_LENGTH` | `8` | Minimum password length |
| `ALLOW_PUBLIC_REGISTRATION` | `false` | Open self-service sign-up (off by default) |
| `CORS_ORIGINS` | localhost | Allowed browser origins |

## Security model

- Passwords are hashed with bcrypt; sessions use a signed JWT delivered as an
  `HttpOnly`, `SameSite=Lax` cookie (and `Secure` outside development).
- Authorization is permission-based (`app/core/permissions.py`); routes declare
  the permission they require.
- All resource access is scoped to the caller's organization.
- Login and setup endpoints are rate-limited to blunt brute-force.
- Public registration is disabled by default; users are provisioned by an admin,
  and the first admin via `/setup`.

Report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## Deploying to production (PaaS)

You don't need to change how the app is packaged — a PaaS deploys the same
Docker image. The container binds to the platform-injected `$PORT`, and the app
normalizes managed-Postgres `postgres://` URLs automatically, so no code changes
are needed to move between platforms.

### Render (turnkey)

A [`render.yaml`](render.yaml) blueprint is included. It provisions the web
service **and** a managed PostgreSQL, runs migrations before each deploy, and
auto-generates strong `SECRET_KEY`/`JWT_SECRET`.

1. Push this repo to GitHub and create a **Blueprint** in Render pointing at it.
2. Render reads `render.yaml`, builds the image, and provisions Postgres.
3. After the first deploy, set `CORS_ORIGINS` to your real origin, e.g.
   `["https://app.example.com"]`.
4. Open the app over HTTPS and complete the `/setup` wizard.

Migrations run via the `preDeployCommand` (`alembic upgrade head`) — they are
**not** baked into the container start command, so replicas never race to
migrate.

### Railway / Fly.io (same container, different glue)

- **Railway** — deploy from the `Dockerfile`, add a managed Postgres plugin
  (provides `DATABASE_URL`), and set the same env vars as `render.yaml`
  (`ENVIRONMENT=production`, `DEBUG=false`, `SECRET_KEY`, `JWT_SECRET`,
  `CORS_ORIGINS`, `ALLOW_PUBLIC_REGISTRATION=false`). Run `alembic upgrade head`
  as a deploy/release step. Railway injects `$PORT`, which the image honors.
- **Fly.io** — `fly launch` detects the `Dockerfile`; add `fly postgres` and set
  the same env vars via `fly secrets`. Use a `[deploy] release_command =
  "alembic upgrade head"` in `fly.toml`. Fly uses a fixed internal port, so
  either leave `$PORT` unset (defaults to 8000) or set it to match your
  `internal_port`.

### Scaling note

The built-in login/setup rate limiter is **per-process** (see
`app/core/ratelimit.py`). It's effective on a single instance, but if you scale
to more than one instance or worker, add an edge rate limiter (the platform's,
or Cloudflare) or a shared store — otherwise each instance counts attempts
independently.

## Production checklist

Before exposing CronQMS publicly:

- [ ] Set `ENVIRONMENT=production` and `DEBUG=false`.
- [ ] Set strong, unique `SECRET_KEY` and `JWT_SECRET` (e.g. `openssl rand -hex 32`).
      The app refuses to start otherwise in a non-dev environment.
- [ ] Serve over HTTPS (required for `Secure` session cookies to work).
- [ ] Use real database credentials — not the `docker-compose` dev defaults.
- [ ] Restrict `CORS_ORIGINS` to your real front-end origin(s).
- [ ] Keep `ALLOW_PUBLIC_REGISTRATION=false` unless you truly want open sign-ups.
- [ ] Put a shared/edge rate limiter (nginx, Cloudflare, gateway) in front if you
      run more than one worker — the built-in limiter is per-process.
- [ ] Run `alembic upgrade head` as part of deployment.

## Testing

```bash
python -m unittest discover -s tests
```

The suite covers the permission matrix, cross-organization isolation, workflows,
and the auth flows.
