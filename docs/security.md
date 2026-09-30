# Security model

What protects what, why each control has the shape it does, and what an operator
has to configure. Written for whoever maintains this next.

No secret values appear here — only the names of things that must be set. This
is a public repository.

For reporting a vulnerability, see [`SECURITY.md`](../SECURITY.md).

---

## What is actually at risk

Three things, and they need different defences:

| | |
|---|---|
| **The athlete's data** | Health and training history, free-text coach conversation. Read by the wrong person, or leaked off-origin, is the worst outcome here. |
| **Stored credentials** | Strava OAuth tokens and user-supplied AI keys, Fernet-encrypted at rest. |
| **The operator's money** | Provider tokens. A stranger who can spend them costs real euros and needs no access to anything else. |

Naming them separately matters, because a control that helps one often does
nothing for another. A captcha stops signup bots; it does not stop a registered
human spending your credit. TOTP protects an account; it is no defence against
automation at all.

## The layers

### Network and transport

Traefik terminates TLS and is the only thing bound to a public port. The backend
publishes on `127.0.0.1` only, so it cannot be reached except through the proxy
(#324). Prometheus and Grafana are likewise loopback-only and carry
`traefik.enable=false` — they are reachable through an SSH tunnel and no other
way. Postgres publishes no port at all.

HSTS, `X-Content-Type-Options` and `Referrer-Policy` are applied by a Traefik
middleware on every TLS router. `Strict-Transport-Security` is set with
`includeSubDomains` — safe because every hostname this stack answers on is
already `websecure`-only — but **not** `preload`, which is a one-way door
enforced by browser vendors rather than by this config.

### Authentication

Authelia is the **user store**, not a forward-auth proxy. This surprises people,
including the person who wrote it down, so it is worth being explicit:

- the forward-auth middlewares exist in `compose.yml` and are attached to **no
  router** — verify with `grep -c 'middlewares=.*authelia@docker' compose.yml`
- `auth_router` reads `users_database.yml` directly and deliberately avoids
  Authelia's `/api/firstfactor`
- therefore `access_control`, its `default_policy: deny`, and
  `session.remember_me` in `authelia/configuration.yml` **never run**

Read on its own that file looks like the app sits behind a deny-by-default
policy. It does not. The app's own JWT is the only access control on `/api`.
Both files carry comments saying so (#688, #696). Gating the backend behind
forward-auth is not a one-line change — it returned 400 to every request and
broke production login once already (#324).

**Passwords** are Argon2id, with legacy bcrypt hashes upgraded transparently on
a successful login (#329). Registration enforces a strength policy that also
rejects fragments of the user's own name and address.

**Second factor** (#688) is TOTP, implemented in the backend rather than in
Authelia — see [Why not Authelia's TOTP](#why-not-authelias-totp). Opt-in per
user. Enrollment stores the secret but leaves the factor **off** until a code
verifies, so someone whose camera fails halfway is not locked out. Recovery
codes are hashed like passwords, single-use, and shown exactly once. A device
can be trusted for 30 days through an `HttpOnly` cookie backed by a revocable
database row.

The admin panel has the same factor through `ADMIN_TOTP_SECRET`. It has no user
record, so the secret is an environment variable and there is deliberately **no
enrollment endpoint** — one less unauthenticated surface in front of the account
that can read every athlete's address and delete any of them.

### Authorisation

Every athlete-facing route lives under `/users/me` and every CRUD call is scoped
by the authenticated user id — `crud.get_race_event(db, current_user.id, event_id)`
rather than by id alone. Admin routes require a separate JWT carrying
`role: admin`.

### Rate limits

All per-process and therefore single-replica; see
[`multi_replica.md`](multi_replica.md).

| Bucket | Limit | Keyed on |
|---|---|---|
| Login | 10 / 5 min | email (lower-cased) |
| Login | 60 / 60 s | global |
| Registration | 5 / hour | global |
| Admin login | 5 / 15 min | global |
| TOTP codes | 5 / 5 min | account |
| Captcha challenges | 120 / 5 min | global |
| AI requests | 10 / min and 100 / hour | account |

Some of these shapes are deliberate rather than obvious:

- **Login counts every attempt, not only failures.** Counting failures alone
  lets one known-good credential reset the window.
- **Login is limited per email *and* globally.** The per-email window is blind
  to one password sprayed across many addresses, which is what a credential dump
  is for. The global bucket is generous on purpose — it is shared, so a tight
  value would let an attacker lock out the legitimate user.
- **Registration and admin login are global.** An attacker picks a fresh address
  each time, so a per-email bucket would never fill; and the admin request
  carries a password and nothing else to key on.

### Spend controls

Two independent limits, because they stop different things: the rate limit stops
a burst, the budget stops a slow drip that never trips it.

`AI_TOKEN_BUDGET` is a rolling per-user ceiling summed from `llm_calls`. **It is
per user, not global** — N accounts means N times the ceiling. It bounds each
account; it does not bound the bill.

`ALLOW_ADMIN_AI_KEY_FALLBACK=false` is what actually closes the money question:
an account without its own provider key can spend nothing. Set it once the
operator has stored a personal key, and check the whole user list first — the
setting affects everyone.

> A route that spends provider tokens and lives outside the `/ai` router gets
> none of that router's dependencies for free. The `.fit` uploads are that
> route, and they have missed a control **twice** — the rate limit (#682) and
> the BYOK context (#693). Any new one needs `set_user_ai_keys` and
> `enforce_ai_rate_limit` listed explicitly.

### Bot defence

Registration is public — it has its own Traefik router — and was found by a
signup bot: nine accounts over two months, none of which ever logged in.

The answer (#686) is a **self-hosted proof-of-work**: the server issues an
HMAC-signed puzzle and `register` refuses anything without a valid, unexpired,
unused solution, before touching the database or computing a hash.

Self-hosted rather than Turnstile or hCaptcha deliberately. Those read
behavioural signals and are better at the underlying question, but they require
loading a third-party script and letting it call home — punching holes in the
CSP that #677 exists to keep shut, and showing every visitor of a self-hosted app
to a third party.

What it buys, stated plainly: an attacker who implements the solver pays
milliseconds per account. What it stops is the *naive* bot — one that POSTs the
form without executing JavaScript cannot obtain a solution at all. The
difficulty therefore matters far less than the protocol requirement.

### Data egress

The coach's reply is model-authored text rendered as markdown, and the prompt
carries the athlete's health data by construction. A remote reference in that
text would beacon on render: no click, nothing visible (#677).

Two independent layers:

1. the markdown component map renders `img` and `a` inert, keeping alt text and
   link targets as plain text so nothing disappears silently
2. a CSP in `frontend/nginx.conf` with `connect-src 'self'`, `img-src 'self'
   data:` and `frame-ancestors 'none'`

The second exists so the guarantee survives a refactor that forgets the first,
and so a compromised frontend dependency has no egress path either. It is also
why the enrollment QR is rendered **server-side as SVG** — a client-side QR
library would have meant an exception.

### Prompt injection

Text this app did not write — activity names from intervals.icu and Strava,
retrieved corpus chunks, the athlete's own prose — is wrapped in data markers
before it reaches the model, and the markers are stripped from the payload first
so the envelope cannot be forged (#680).

**Marking is not a proof**, and `services/untrusted_text.py` says so. A
determined instruction inside a marked span can still sway a model. The real
containment is elsewhere and stays there: the LLM has **no tools**
(`services/llm.py` exposes only `chat`/`chat_history`), plan writes go through
`plan_pipeline`'s constraint gates, and the reply cannot reach a third-party
host.

### At rest

`SECRETS_ENCRYPTION_KEY` is a Fernet key encrypting every secret stored on a
user's behalf: Strava tokens, intervals.icu keys, user AI keys, and TOTP
secrets. The backend **refuses to boot** without it outside development, and
validates that it is a usable Fernet key rather than discovering that on the
first request (#612/#613).

Rotating it makes every stored token unreadable — users have to reconnect.

### Container

The backend runs as a non-root user. `APP_UID` must match the owner of the
bind-mounted `authelia/` directory on the host, because a bind mount keeps the
host's ownership rather than the image's. The backend checks this at startup and
logs a warning if it cannot write there.

---

## Decisions worth understanding before changing them

### Why not Authelia's TOTP

Two independent reasons. Nothing routes through Authelia (see above), so a
factor configured there would never run. And Authelia's own enrollment
**requires a notifier** — it emails an identity-confirmation link before showing
the QR — while this deployment has no working SMTP (#687). Implemented in the
backend, the feature needs neither.

### Why the login challenge derives its key

Adding a second step means the server must remember, between two requests, that
a password was already accepted — without trusting the client to say so. The
challenge is a signed statement to that effect, HMAC'd with a key **derived from
`JWT_SECRET`** rather than from a setting of its own.

That is not laziness. Every secret added lately had to be threaded through four
places, and #617, #684 and #694 are all cases where one was missed and the
result was a *silent fallback* rather than an error. A derived key cannot be
half-configured. The captcha uses the same approach.

### Why one login exit

Both branches of `login` used to end by issuing a token. That is precisely the
shape that lets a control be enforced on one path and forgotten on the other.
They now share `_complete_login`, and `/auth/session` — which mints a token from
an Authelia portal session *without ever seeing a password* — goes through it
too. That endpoint is currently unreachable, but by routing accident rather than
design.

### Why the Authelia config is mounted read-only

Authelia's entrypoint chowns `/config` to the user it runs as (root) on **every
start**, while the backend reads `users_database.yml` as a non-root user. That
took login down twice (#684, #696): `PermissionError`, 500 on every login, for
everyone.

`:ro` removes the write instead of racing it. Safe only because Authelia merely
reads that directory — its storage is a separate volume, and password reset, the
one flow that would rewrite the user store, is disabled.

> **Re-enabling password reset breaks this.** The mount would have to become
> writable again, restoring the ownership conflict. The fix then is running
> Authelia as the same uid as the backend, not simply dropping `:ro`.

### Why a second factor is not a bot defence

TOTP proves possession of a secret the server just issued. A script enrolls its
own device and computes valid codes in a few lines. It only impedes automated
signup when enrollment runs through an out-of-band human channel — at which
point it is the email doing the work, not the TOTP.

| Goal | Tool |
|---|---|
| Bots | proof-of-work (#686) |
| Throwaway addresses | email verification (#687, deferred) |
| A leaked password | TOTP (#688) |
| A stranger spending your credit | BYOK-only |

---

## Operator checklist

Names only. Values belong in the GitHub environment and in `.env` on the host.

### Required — the backend refuses to start without these

| | |
|---|---|
| `POSTGRES_PASSWORD` | no default anywhere; a placeholder here is silently wrong rather than merely insecure |
| `SECRETS_ENCRYPTION_KEY` | Fernet key; without it, stored tokens fall back to plaintext |
| `JWT_SECRET` | ≥ 32 bytes; also derives the captcha and TOTP challenge keys |
| `AUTHELIA_SESSION_SECRET` | |
| `AUTHELIA_STORAGE_ENCRYPTION_KEY` | |
| `AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET` | |

The three Authelia values are required because they once rendered **empty**, and
compose substituted a placeholder committed to this public repository (#684). An
empty value and a missing one are not the same thing to `${VAR:-default}`.

### Worth setting

| | |
|---|---|
| `ADMIN_PASSWORD` | empty disables the admin panel entirely |
| `ADMIN_TOTP_SECRET` | generate with `uv run python -m scripts.generate_admin_totp`; empty leaves the panel on password plus rate limit |
| `AUTHELIA_PROXY_SHARED_SECRET` | defence in depth for `Remote-*` header trust |
| `ALLOW_ADMIN_AI_KEY_FALLBACK` | `false` requires every account to bring its own provider key |
| `AI_TOKEN_BUDGET` | per user per 30 days; `0` disables the ceiling |
| `APP_UID` / `APP_GID` | must match the owner of `authelia/` on the host |

### Where the deployment lives

Production secrets and host configuration belong in the private companion
repository `ai-trainer-ops`, not here. A merge to `develop` dispatches its deploy
workflow with the commit SHA; it checks out this repository at that commit —
public, so no credential is involved — and ships it.

The dispatch travels one way and carries nothing but a SHA. This repository
cannot read anything in ops.

The cutover is complete. The workflow here is one `dispatch` job, and the only
secret it needs is the dispatch token — which can trigger a deploy in ops and
do nothing else.

It did not go cleanly, and the failure is the useful part. The first attempt
kept a fallback deploy job here, guarded on whether `OPS_DISPATCH_TOKEN` was
readable. That token is an **environment** secret, and the job testing for it
did not declare `environment: production` — an environment secret read from a
job with no environment is the empty string, not an error. The gate concluded
"not wired up", skipped the dispatch, and the fallback deployed successfully
over the path the move existed to retire (#700).

### How a deployment variable reaches production

This used to be a four-place rule: `compose.yml`, the env template, the
workflow's `env:` block, and the workflow's `extra_vars` dict. Miss either of
the last two and the value silently took the template default — the deploy
succeeded, the container came up healthy, and the setting was quietly not what
you set. That cost something three times: #617, #684, #694.

The two workflow places are gone. The ops workflow forwards the whole `secrets`
and `vars` contexts and names nothing, so there is no list to forget:

1. `deploy/ansible/templates/app.env.j2` — the line that reads it
2. `deploy/forwarded-vars.yml` — the entry saying where it comes from, and what
   its absence means (`required`, `optional`, `omit_if_empty`, `fallback`)
3. `compose.yml` — `KEY: ${KEY:-default}`, only if a container needs it

`deploy/render_extra_vars.py` applies the manifest and fails naming the variable
*before* Ansible starts, which matters because the playbook renders `.env` only
after it has rsynced the repo.

All three places are in this public repository on purpose. Moving the deploy out
would otherwise have taken the guard with it: `test_deploy_wiring.py` used to
read the workflow, and the workflow was about to become invisible here. It now
checks the manifest against the template, pins the six values whose absence
must never render as empty, and rejects forwarding that nothing reads.

What it still cannot check is whether a `mode` is *right* — `optional` on
something that must never be blank would pass the general test, which is why
those six are asserted by name.

### After a deploy

A hand-edit of `.env` on the host survives only until the next deploy, which
re-renders it from the template. Worth confirming:

```
docker compose ps
docker exec ai-trainer-backend-1 id                    # expected: the APP_UID user
ls -lan authelia/users_database.yml                    # owner must match
docker compose logs --since 5m backend | grep -i warning
```

---

## Known limitations

Recorded because a maintainer needs them, and because they are visible in the
code anyway.

**JWT has no revocation.** Tokens are valid for seven days and carry no `jti`.
Logging out or changing a password does not invalidate one already issued, and
enabling TOTP does not either. `get_current_user` re-reads the user on every
request, so a *deleted* account loses access immediately. Rotating `JWT_SECRET`
invalidates everything at once.

**Rate limits are per process.** Windows live in module memory, so with N
replicas each key gets N times the allowance. This degrades gracefully for the
AI limits, less so for the auth ones — a multi-replica deployment should treat a
shared counter as a prerequisite. See [`multi_replica.md`](multi_replica.md).

**The global request-size limit reads `Content-Length`.** A chunked request
carries none and slips past it. It is a backstop; the routes that actually read
a large body cap themselves while reading, and that is the guarantee.

**Registration is open.** Deliberate, and bounded by the proof-of-work and the
rate limit rather than closed. Email verification (#687) would tighten it.

**Proof-of-work is not behavioural.** See the note above on what it does and
does not buy.

---

## Issue trail

The reasoning behind each control is in its issue, and the code comments point
at them rather than repeating them.

| | |
|---|---|
| #324 | Authelia is the user store, not an SSO proxy |
| #612 / #613 | encryption at rest, and the key that silently did nothing |
| #676 / #677 / #680 | AI spend controls, coach egress, prompt-injection marking |
| #682 | auth brute force, body limits, non-root container, HSTS |
| #684 | atomic write losing file ownership; the token budget set below real usage |
| #686 | registration proof-of-work |
| #687 | email verification and SMTP — deferred, with reasons |
| #688 | TOTP second factor |
| #693 | BYOK bypassed by the `.fit` upload routes |
| #694 | a deploy silently reverting a spend control |
| #696 | Authelia's entrypoint re-owning the shared config mount |
