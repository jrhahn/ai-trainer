# AI Trainer — Smart Cycling Coach

[![Frontend Coverage](https://codecov.io/gh/jrhahn/ai-trainer/graph/badge.svg?flag=frontend)](https://codecov.io/gh/jrhahn/ai-trainer)
[![Backend Coverage](https://codecov.io/gh/jrhahn/ai-trainer/graph/badge.svg?flag=backend)](https://codecov.io/gh/jrhahn/ai-trainer)

A smart cycling training app powered by AI (OpenAI or Google Gemini) with Strava integration.

**New here?** [Getting started](docs/getting-started.md) walks you through Strava → intervals.icu →
Train Like a Pro and your Google Gemini key in about 10 minutes.
([Deutsche Fassung](docs/erste-schritte.md))

**Running your own instance?** [Security model](docs/security.md) covers the
controls, what has to be configured, and the limitations that are known and
accepted. Reporting a vulnerability: [SECURITY.md](SECURITY.md).

## License: source available, not open source

This code is published under the [Functional Source License](LICENSE)
(`FSL-1.1-ALv2`), which is **source available**, not open source. The difference
is deliberate and it is worth two sentences of your time.

**Why it is published at all.** A coach that makes claims about your training
and your health should be auditable. The physiological model here is
deterministic and carries its evidence and its confidence with every number, and
the point of that is lost if you have to take it on faith. So: read it, check it,
disagree with it.

**What you may do.** Read the code, modify it, and run your own instance for
yourself — `for your internal use and access` is an explicitly permitted purpose.
Non-commercial research and education too. Every version additionally becomes
Apache 2.0 two years after its release, automatically and irrevocably.

**What you may not do.** Offer it to others as a commercial product or service.
That is the one thing reserved, because hosting it is how this is paid for.

If you want to do something the license does not cover — running it for the
athletes you coach, for instance — ask. The answer is often yes, and it has to be
in writing to count.

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
Authelia is the file-backed user store for production registration and login,
while the app uses its own JWT for API authorization after sign-in. Local Compose keeps
Authelia notifications in `/data/notification.txt` for password reset and future
identity-verification flows.

> **Security note (issue #324, ai-trainer-ops#34):** Authelia is the file-backed
> **user store** here, not an SSO forward-auth proxy — the backend authenticates
> with its own JWT issued by `/api/v1/auth/login` and `/register`. The backend
> must therefore **not** be gated behind Authelia, and it must never trust
> inbound `Remote-User`/`Remote-Email`/`Remote-Name` identity headers.
>
> The backend ignores `Remote-*` unless a request carries
> `AUTHELIA_PROXY_SHARED_SECRET` in the header the trusted proxy injects — and an
> **unset** secret means ignore, not trust. That is the boundary. Everything else
> is defence in depth behind it: Traefik deletes those headers on the backend
> routes (`backend-strip-remote` in `compose.yml`), the frontend nginx blanks them
> on its own `/api` proxy, and the backend port binds to `127.0.0.1` so it is
> only reachable through a proxy at all.
>
> This paragraph used to call the secret check belt-and-suspenders and say the
> proxy never injected it. Both halves were wrong: the proxy has injected it
> since #682, an empty secret meant *trust the headers*, and the secret is
> `optional` in `deploy/forwarded-vars.yml` — so on this deployment the two
> header-strip lists were the only thing between a forged `Remote-Email` and a
> session token for any account. `backend/tests/test_remote_user_boundary.py` is
> that attack, and it now runs in CI.

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

## How it works

```
Strava API ──► FastAPI backend ──► AI coaching response
                    │
                    ▼
           pgvector (PostgreSQL)
           cycling science knowledge base
           (RAG via text-embedding-3-small)
```

The coaching assistant combines two sources of context before calling the LLM:

1. **Retrieval-Augmented Generation (RAG)** — a cycling science knowledge base
   (`backend/knowledge/`) is chunked, embedded with `text-embedding-3-small`, and
   stored in PostgreSQL via `pgvector`. At query time the most relevant chunks are
   retrieved by cosine similarity and injected into the prompt.

2. **Athlete context** — real activity data pulled from the Strava API (power,
   heart rate, elevation, cadence streams) is analysed and summarised alongside
   the user's training plan and feedback history.

The LLM (OpenAI GPT-4o or Google Gemini, switchable via `AI_PROVIDER` env var)
receives both sources and returns structured coaching advice. The prompt
engineering layer lives in `backend/services/prompts.py`.

See [`docs/update_rag.md`](docs/update_rag.md) for how to refresh the knowledge base.

## Features

- **AI-powered training plans** — generate and adapt cycling plans via OpenAI or Google Gemini
- **Strava integration** — connect your Strava account to pull in real activity data
- **RAG knowledge base** — responses grounded in cycling science literature via pgvector
- **Local-first** — all user data (profile, plan, feedback) persisted in browser `localStorage` via Zustand

## Building for production

```bash
cd frontend
npm run build   # outputs to frontend/dist/
```

## Deployment (Hetzner)

This repository includes Ansible-based deployment for a Debian Hetzner VPS:

- Playbook: `deploy/ansible/deploy.yml`
- Env template: `deploy/ansible/templates/app.env.j2`
- Deployment inputs: `deploy/forwarded-vars.yml` (+ `deploy/render_extra_vars.py`)
- Workflow: `.github/workflows/deploy.yml`

A push to `develop` dispatches the deploy, which runs from a **separate private
repository** holding the production secrets and host configuration. This
repository keeps none of them — the dispatch carries only a commit SHA, and the
deploy checks this repository out at that commit without any credential, because
it is public.

What does live here is the contract. `deploy/forwarded-vars.yml` lists every
deployment input, where it comes from (GitHub secret or repository variable),
and what its absence means; `deploy/render_extra_vars.py` applies it and fails
naming the variable before Ansible starts. To add a setting, add the line to
`app.env.j2` and the entry to the manifest — `backend/tests/test_deploy_wiring.py`
checks the two agree. See [`docs/security.md`](docs/security.md) for why it is
shaped that way.

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
