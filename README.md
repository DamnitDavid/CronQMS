# Proins

Proins is a **Quality Event Management System (QMS)** for manufacturing SMBs. It
tracks the quality lifecycle end to end — events, investigations, corrective and
preventive actions, controlled documents, audits, training, and change control —
with role-based access control and an append-only audit trail.

Built with FastAPI, SQLAlchemy, Jinja2 + htmx (server-rendered UI), and
PostgreSQL, with schema managed by Alembic.

## Features

- **Quality Events** — capture, investigate, and close events with a governed
  status workflow and configurable custom fields.
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

### Option A — Docker Compose (recommended for local dev)

```bash
cp .env.example .env          # then edit values (see Configuration)
docker compose up --build
```

This starts PostgreSQL, runs `alembic upgrade head`, and serves the app at
http://localhost:8000. On first run, open the app and you'll be sent to the
`/setup` wizard to create the first organization and admin.

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

## Production checklist

Before exposing Proins publicly:

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
