# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.7.3 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

RUN chmod +x entrypoint.sh

# Drop root (#682). The process needs no write access to the image: logging.yaml
# uses StreamHandlers only, PYTHONDONTWRITEBYTECODE is set, and Alembic writes
# to the database rather than to disk.
#
# The one writable path is the bind-mounted Authelia user store
# (./authelia:/authelia-config), whose ownership comes from the *host*, not from
# here — so APP_UID must match the owner of that directory on the deploy host,
# or registration starts failing. 1000 is the first non-root user on a stock
# Debian/Ubuntu host, which is what the Ansible deploy runs as. The backend
# checks this at startup and logs a warning if it cannot write there
# (``warn_if_authelia_user_store_unwritable``), so a mismatch is visible in the
# logs rather than only in a failed sign-up.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin app \
    && chown -R "${APP_UID}:${APP_GID}" /app

USER app

EXPOSE 8000

CMD ["./entrypoint.sh"]