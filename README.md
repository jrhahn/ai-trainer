# AI Trainer — Smart Cycling Coach

[![Frontend Coverage](https://codecov.io/gh/jrhahn/ai-trainer/graph/badge.svg)](https://codecov.io/gh/jrhahn/ai-trainer)
[![Backend Coverage](https://codecov.io/gh/jrhahn/ai-trainer/graph/badge.svg?flag=backend)](https://codecov.io/gh/jrhahn/ai-trainer)

A smart cycling training app powered by AI (OpenAI or Google Gemini) with Strava integration.

## Repository structure

```
ai-trainer/
├── frontend/   # Vite + React + TypeScript SPA
└── backend/    # Python FastAPI server (Strava OAuth)
```

## Quick start

### Docker Compose

```bash
cp .env.example .env
# fill in the secrets you want to use
docker compose up -d
```

This starts:

- frontend on <http://localhost:5173>
- backend on <http://localhost:8000>
- traefik reverse proxy on <http://localhost> and <https://localhost>
- postgres inside the compose network with a persistent named volume (`postgres_data`)
- authelia for SSO/session-based user management (state persisted in `authelia/`)

The backend can boot with placeholder AI and Strava credentials, but those
features will only work after you set real values in `.env`.
Before first deploy, replace the placeholder Authelia user in
`authelia/users_database.yml` with your real admin account.
Authelia enforces TOTP for secure sign-in and protected API routes while the
frontend app shell stays publicly reachable. The login button redirects to
Authelia, then returns to `/auth/callback` to exchange the verified session for
an app token. Local Compose keeps
Authelia notifications in `/data/notification.txt`; production deployments
should provide SMTP settings and include `compose.smtp.yml` so users can verify
TOTP enrollment by email.

### Backend (Strava OAuth)

```bash
cd backend
cp .env.example .env   # fill in STRAVA_CLIENT_ID + STRAVA_CLIENT_SECRET
uv sync
uv run uvicorn main:app --reload --port 8000
```

See [`backend/README.md`](backend/README.md) for full setup instructions.

### Frontend

```bash
cd frontend
cp .env.example .env.local   # set VITE_BACKEND_URL=http://localhost:8000
npm install
npm run dev
```

The app will be available at <http://localhost:5173>.

## Features

- **AI-powered training plans** — generate and adapt cycling plans via OpenAI or Google Gemini
- **Strava integration** — connect your Strava account to pull in real activity data
- **Local-first** — all user data (profile, plan, feedback) persisted in browser `localStorage` via Zustand

## Building for production

```bash
cd frontend
npm run build   # outputs to frontend/dist/
```

## Deployment (Hetzner)

This repository includes Ansible-based deployment for a Debian Hetzner VPS:

- Playbook: `deploy/ansible/deploy.yml`
- Workflow: `.github/workflows/deploy.yml`

The deployment workflow is tied to the `production` environment. If that
environment is configured with required reviewers, deployment waits for manual
maintainer approval. If required reviewers are not available (for example on
free plans), deployment runs automatically after pushes to `develop`.

Traefik is configured as the public reverse proxy with Let's Encrypt TLS:

- `trainlikea.pro` and `train-like-a.pro` (and any subdomain) route to the frontend
- backend API routes (`/api`, `/healthz`) are proxied through the same domains
- `auth.trainlikea.pro` and `auth.train-like-a.pro` route to Authelia

DNS records must point to the server:

- `A/AAAA trainlikea.pro`
- `A/AAAA *.trainlikea.pro`
- `A/AAAA train-like-a.pro`
- `A/AAAA *.train-like-a.pro`

Set `ACME_EMAIL` in `.env` (or deployment vars) to receive certificate notices.
