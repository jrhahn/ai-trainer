# AI Trainer – Strava OAuth Backend

A lightweight Python / FastAPI server that holds the Strava app credentials so
end-users never have to register their own Strava application.

## How it works

```
User clicks "Connect Strava"
  → frontend navigates to  GET /auth/strava
  → backend redirects user  →  Strava OAuth page
  → Strava redirects back   →  GET /auth/strava/callback?code=…
  → backend exchanges code for tokens
  → backend redirects user  →  <frontend>/strava/callback?access_token=…&…
  → frontend stores tokens in localStorage
```

The Strava Client ID and Client Secret live only in this server's environment
variables and are never sent to the browser.

## Quick start

### 1. Register a Strava application (once, by you as the app owner)

1. Go to <https://www.strava.com/settings/api> and create a new application.
2. Set **Authorization Callback Domain** to your server's domain (e.g. `localhost`
   for local dev, or `api.yourdomain.com` for production).
3. Copy the **Client ID** and **Client Secret**.

### 2. Configure environment variables

```bash
cp .env.example .env
# then edit .env and fill in STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET
```

| Variable              | Description                                              | Default                    |
|-----------------------|----------------------------------------------------------|----------------------------|
| `STRAVA_CLIENT_ID`    | **Required.** Your Strava app's Client ID               | –                          |
| `STRAVA_CLIENT_SECRET`| **Required.** Your Strava app's Client Secret           | –                          |
| `FRONTEND_URL`        | URL where the React app is served                        | `http://localhost:5173`    |
| `BACKEND_URL`         | Public URL of this backend (must match Strava's callback)| `http://localhost:8000`    |

### 3. Install dependencies

```bash
cd backend
uv sync
```

`uv sync` reads `pyproject.toml` and `uv.lock`, creates a `.venv` automatically,
and installs all dependencies in one step — no separate `python -m venv` or `pip install` needed.

### 4. Run the server

```bash
uv run uvicorn main:app --reload --port 8000
```

Interactive API docs are available at <http://localhost:8000/docs>.

### 5. Configure the frontend

In the React app root, create (or edit) `.env.local`:

```
VITE_BACKEND_URL=http://localhost:8000
```

Then restart the Vite dev server (`npm run dev`).

## Production deployment

Deploy this server on any platform that supports Python (Render, Railway,
Fly.io, AWS Lambda via Mangum, etc.).  Set `BACKEND_URL` to your server's
public HTTPS URL and update the **Authorization Callback Domain** in your
Strava app settings to match.

Most platforms detect `pyproject.toml` and run `uv sync` automatically.
For platforms that need an explicit start command, use:

```bash
uv run uvicorn main:app --host 0.0.0.0 --port $PORT
```

## API reference

| Method | Path                    | Description                            |
|--------|-------------------------|----------------------------------------|
| GET    | `/healthz`              | Health check                           |
| GET    | `/auth/strava`          | Initiate Strava OAuth flow             |
| GET    | `/auth/strava/callback` | Strava OAuth callback (code exchange)  |
| POST   | `/auth/strava/refresh`  | Refresh an expired access token        |
