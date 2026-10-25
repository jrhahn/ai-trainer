# AI Trainer — Smart Cycling Coach

[![Frontend Coverage](https://codecov.io/gh/jrhahn/ai-trainer/graph/badge.svg?flag=frontend)](https://codecov.io/gh/jrhahn/ai-trainer)
[![Backend Coverage](https://codecov.io/gh/jrhahn/ai-trainer/graph/badge.svg?flag=backend)](https://codecov.io/gh/jrhahn/ai-trainer)

A cycling coach powered by AI (Google Gemini or OpenAI) that plans your training
from the rides you record, through intervals.icu.

**New here?** [Getting started](docs/getting-started.md) walks you through your device →
intervals.icu → Train Like a Pro in about 10 minutes, with screenshots. A Google Gemini
key of your own is optional. ([Deutsche Fassung](docs/erste-schritte.md))

**Running your own instance?** [Security model](docs/security.md) covers the
controls, what has to be configured, and the limitations that are known and
accepted. [Backup and restore](docs/runbook-restore.md) is the procedure for
getting a deployment back, and the record of when it was last rehearsed.
Reporting a vulnerability: [SECURITY.md](SECURITY.md).

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
├── frontend/   # Vite + React + TypeScript single-page app
├── backend/    # FastAPI: the coach, the plans, the physiological model, the activity sync
├── deploy/     # the deployment contract (Ansible template, forwarded variables)
├── docs/       # guides for athletes and operators
└── scripts/    # backup, restore and the Compose smoke test
```

## Quick start

### Local development

Needs [uv](https://docs.astral.sh/uv/) and Node 22.

```bash
cd backend
uv sync
DATABASE_URL=sqlite+aiosqlite:///./app.db uv run uvicorn main:app --reload --port 8000
```

```bash
cd frontend
npm ci
npm run dev
```

The app is at <http://localhost:5173>. It talks to the backend at
<http://localhost:8000>, whose API docs are at `/docs`.

In development the backend creates its SQLite tables from the models and needs
no Authelia. A new account signs in without a confirmation mail, because no mail
server is set.

For the coach, put `GEMINI_API_KEY` (or `OPENAI_API_KEY`) in `backend/.env`, or
enter a key in the app's settings. Every other setting, with its default, is in
[`.env.example`](.env.example).

### The production stack (Docker Compose)

```bash
cp .env.example .env
# replace every placeholder secret; the comments say how to generate each
docker compose up -d
```

`compose.yml` is the stack the deployment runs:

- traefik on ports 80 and 443, with Let's Encrypt certificates. It routes the
  domains named in `compose.yml` to the frontend, to the backend's `/api` and
  to the Authelia portal.
- the backend, which applies the migrations when it starts. It also listens on
  `127.0.0.1:8000`.
- postgres with pgvector, in a named volume (`postgres_data`)
- authelia as the file-backed user store (`authelia/`)
- prometheus and grafana, on the loopback interface only

`python3 scripts/compose_smoke.py` starts this stack from `HEAD` with throwaway
secrets and signs in through it. CI runs it on every pull request, as the
"Compose stack signs in" check.

The backend can boot with placeholder AI and Strava credentials, but those
features will only work after you set real values in `.env`.
Before first deploy, replace the placeholder Authelia user in
`authelia/users_database.yml` with your real admin account.
Authelia is the file-backed user store for production registration and login,
while the app uses its own JWT for API authorization after sign-in. Local Compose
keeps Authelia notifications in `/data/notification.txt` for future
identity-verification flows.

**Password reset** is the app's own, not Authelia's (ai-trainer-ops#35). The
backend mails a one-time link through a transactional provider and writes the
new password to `authelia/users_database.yml` the way registration does.
Authelia's own flow stays off, because it would need the config mount writable
(see `docs/security.md`). The reset is offered only when `SMTP_HOST` and
`MAIL_FROM` are set (`compose.yml`, `deploy/forwarded-vars.yml`). Without them, a
forgotten password still means editing the user store by hand.

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

## How it works

```
intervals.icu (or Strava) ──► FastAPI backend ──► Gemini or OpenAI ──► coach reply, plan updates
                                   │
                                   ▼
                        PostgreSQL + pgvector
              (rides, plans, coach memory, knowledge base)
```

The coach combines two sources of context before it calls the model:

1. **Retrieval-Augmented Generation (RAG):** a cycling-science knowledge base
   (`backend/knowledge/`) is chunked and embedded, then stored in PostgreSQL via
   `pgvector`. The embeddings are Gemini's `gemini-embedding-001` by default;
   `EMBEDDING_PROVIDER` switches to OpenAI's. At query time the most relevant
   chunks are retrieved by cosine similarity and go into the prompt.

2. **Athlete context:** the rides come from intervals.icu, or from Strava where
   a deployment connects it directly. A deterministic physiological model
   analyses them for training load, fitness and fatigue, and the power curve,
   and it carries its evidence and its confidence with every number. The plan,
   the athlete's feedback and what the coach has learned about them go in
   alongside.

Each athlete's coach runs on Google Gemini by default, or on OpenAI. The model
for each task is set in `backend/config.py`.
- An athlete can bring a key of their own.
- The operator's key covers the other athletes while
  `ALLOW_ADMIN_AI_KEY_FALLBACK` allows it.

The prompts live in `backend/services/prompts.py`.

See [`docs/update_rag.md`](docs/update_rag.md) for how to refresh the knowledge base.

## Features

- **A coach to talk to:** it answers from the athlete's own rides and plan, and
  changes the plan when asked.
- **Plans that adapt:** generated from the athlete's goals and the days they
  can train, then adapted after new rides and every night.
- **A model you can check:**
  - fitness, fatigue, form and the power curve are computed, not guessed;
  - each number shows its evidence and its confidence.
- **Activities through intervals.icu,** which collects them from the athlete's
  device or platform. A direct Strava connection is optional.
- **Grounded answers:** a cycling-science knowledge base via pgvector.
- **Accounts on the server:**
  - PostgreSQL holds the data;
  - sign-in by password with an optional second factor;
  - the app is in English and German.

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

The deploy runs from a **separate private repository** holding the production
secrets and host configuration. It polls `develop` and deploys a new commit
within about ten minutes. This repository keeps none of its secrets, and the
deploy fetches this repository at that commit without any credential, because
it is public.

What does live here is the contract. `deploy/forwarded-vars.yml` lists every
deployment input, where it comes from (repository secret or variable),
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
