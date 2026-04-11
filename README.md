# AI Trainer — Smart Cycling Coach

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
- postgres inside the compose network with a persistent named volume

The backend can boot with placeholder AI and Strava credentials, but those
features will only work after you set real values in `.env`.

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

