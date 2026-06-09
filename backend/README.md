# AI Trainer – Strava OAuth Backend

A lightweight Python / FastAPI server that holds the Strava app credentials so
end-users never have to register their own Strava application.

## How it works

```
User clicks "Connect Strava"
  → frontend calls         GET /auth/strava (with bearer token)
  → backend returns OAuth URL with short-lived server-side state
  → frontend redirects user →  Strava OAuth page
  → Strava redirects back   →  GET /auth/strava/callback?code=…
  → backend exchanges code for tokens
  → backend redirects user  →  <frontend>/strava/callback?success=true
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
| `SERVER_URL`          | Bare public domain (e.g. `trainlikea.pro`); `https://` is prepended automatically to build the Strava callback URL. Takes priority over `BACKEND_URL`. | – |
| `BACKEND_URL`         | Full public URL fallback when `SERVER_URL` is not set (local dev) | `http://localhost:8000` |
| `APP_ENV`             | Runtime environment. Set to `production` (or `staging`) for deployments. The app refuses to start if `JWT_SECRET` is the default insecure value, or shorter than 32 bytes for HS256, and `APP_ENV` is not a dev/test environment. | `development` |

### 3. Install dependencies

```bash
cd backend
uv sync
```

`uv sync` reads `pyproject.toml` and `uv.lock`, creates a `.venv` automatically,
and installs all dependencies in one step — no separate `python -m venv` or `pip install` needed.

> **Tip – automatic activation with direnv**
>
> If you have [direnv](https://direnv.net) installed and hooked into your shell,
> simply run `direnv allow` once inside the `backend/` directory. direnv will
> then run `uv sync` (if `.venv` is missing) and activate the virtual
> environment every time you `cd` into the folder.
>
> ```bash
> cd backend
> direnv allow   # one-time approval
> # .venv is created and activated automatically from here on
> ```

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
Fly.io, AWS Lambda via Mangum, etc.).  Set `SERVER_URL` to your server's
public domain (e.g. `trainlikea.pro`) and update the **Authorization Callback Domain** in your
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
| GET    | `/auth/session`         | Mint JWT from Authelia forwarded session |
| GET    | `/auth/strava`          | Return Strava OAuth URL (auth required)|
| GET    | `/auth/strava/callback` | Strava OAuth callback (code exchange)  |
| POST   | `/auth/strava/refresh`  | Refresh an expired access token        |
