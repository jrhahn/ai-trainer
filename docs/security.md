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

**`Remote-*` identity headers are ignored unless a request proves it transited
the trusted proxy**, and an unset `AUTHELIA_PROXY_SHARED_SECRET` means ignored,
not trusted (ai-trainer-ops#34). It used to mean trusted, which read as defence
in depth and inverted it: the secret is `optional` in
`deploy/forwarded-vars.yml`, so on this deployment a forged `Remote-Email`
authenticated as any athlete with no bearer token at all, and
`/api/v1/auth/session` turned it into a seven-day JWT. What stood in the way was
two hand-maintained header-strip lists — Traefik's `backend-strip-remote` and
the frontend nginx's `proxy_set_header` block — either of which is one label
edit or one proxy upgrade from not holding.

The attack is `backend/tests/test_remote_user_boundary.py`, in three layers that
have to meet: a real uvicorn driven with raw bytes decides which header shapes
can arrive, the real app decides what it does with each of them, and
`compose.yml` plus `frontend/nginx.conf` must delete exactly the names the app
honours on every route that reaches the backend. The first layer is what makes
the third finite — uvicorn lower-cases every header name, so "the names the app
honours" is three strings and a strip list can be checked against it. What no
test here can reach is the Traefik half: whether a *front* proxy normalises a
malformed header into a well-formed one needs the real pair.

**Passwords** are Argon2id, with legacy bcrypt hashes upgraded transparently on
a successful login (#329). Registration enforces a strength policy that also
rejects fragments of the user's own name and address.

**A login costs the same whether the address has an account or not**
(ai-trainer-ops#35). It did not: both password paths wrote `user is None or not
verify_password(...)`, which skipped the Argon2 verification entirely for an
unknown address. Argon2 is deliberately expensive, so the short-circuit answered
in 8 ms against 165 ms — a twentyfold tell, from one request, with no averaging
needed. `auth.password_matches` now takes `None` for "no such account" and
spends a verification against a stand-in hash, and the decision lives in that
one function rather than at each call site, where the next caller would write
the shortcut again. The Authelia store path had the same gap plus one of its
own: a `disabled` entry answered fast too, which told an attacker the address
exists and is switched off.

What is *not* hidden is registration: 409 for a taken address against 200 for a
free one. Hiding it means accepting the signup and emailing the owner instead,
which needs the SMTP this deployment does not have (#687) and tells the owner
something an attacker can then trigger at will. It stays visible and bounded
instead — `enforce_registration_rate_limit` runs before the lookup, so probing
costs the same five-per-hour global allowance a real signup does.

**There is no password-reset flow, and no way to change a password.** An athlete
who forgets theirs has no way back in. That is a product gap waiting on #687,
not a hardened decision — but it is also why the questions "how random is the
reset token" and "what invalidates a session on a password change" currently
have no answer to get wrong. `test_auth_hardening.py` fails the day either route
appears, so whoever adds one has to answer them.

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

### Sessions and revocation

The access token is a JWT, valid for seven days, held in `sessionStorage` so it
dies with the tab rather than outliving the browser.

It is **revocable** (#704). `users.token_generation` is a counter; every token
carries the value it had when the token was minted, and `get_current_user`
refuses a token whose claim no longer matches the row. The user row is loaded
there already, so this costs no extra query.

Two things bump the counter, and both end every session for the account at
once — there is no per-device revocation:

| | |
|---|---|
| `POST /auth/sessions/revoke` | the athlete's "sign out everywhere". Needs the password, like turning the second factor off, and signs the caller out too — it genuinely invalidates the token that made the request. Trusted devices go with it. |
| `POST /admin/users/{id}/revoke-sessions` | the operator's version, for a leak reported by someone who cannot sign in, or a password changed by hand in `users_database.yml` — which the app's tokens know nothing about. |

**Deleting the account is the third thing that ends a session**, and it needs no
counter: `get_current_user` loads the user row to authenticate, so the token
401s by itself once the row is gone. Two things were missing there
(ai-trainer-ops#35). `DELETE /users/me` took a bare session, while both
neighbouring step-up routes asked for the password on the argument that locking
the owner out must cost more than a borrowed unlocked browser — deletion is that
argument's strongest case. And with header auth on, the password lives in
`users_database.yml`, which neither deletion route touched; since `POST
/auth/login` recreates a missing row from a valid credential, the account came
back on the next sign-in with a fresh token. Both deletion routes now go through
`services/authelia_store.delete_user`, which exists because the knowledge of
that file used to live in `auth_router` — a module neither `routers/users.py`
nor `routers/admin.py` may import, which is precisely why neither called it.

Admin tokens have no user row to count against, so they are bound to the
credentials instead: the token carries an HMAC over `ADMIN_PASSWORD` and
`ADMIN_TOTP_SECRET`, checked in `require_admin`. Rotating either invalidates
every admin token issued under the old one, which matters because rotating the
admin password is exactly what you do when you suspect one leaked.

A token minted before #704 carries no claim, which reads as 0 — the value the
migration gives every existing row. Deploying it therefore signed nobody out.

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
- **Nothing is keyed on the client address.** It arrives through Traefik *and*
  nginx, so trusting it needs a trusted-proxy hop count nothing in this app
  establishes — and a limit keyed on a header the caller can set is worse than
  no limit, because it reads as one. The cost is accepted on both sides: a
  shared NAT cannot lock its neighbours out of accounts that are not theirs, and
  an attacker with many addresses gets no extra allowance for them. Pinned by
  `test_auth_hardening.py`, which reads the limit functions and fails if one
  starts consulting `request.client` or a forwarded header.

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

Every secret stored on an athlete's behalf is Fernet-encrypted in its column,
and since ai-trainer-ops#12 **each kind has its own key**:

| Purpose | Columns | Key |
|---|---|---|
| Strava OAuth | `strava_tokens.access_token`, `.refresh_token` | `STRAVA_TOKEN_ENCRYPTION_KEY` |
| intervals.icu | `intervals_tokens.api_key` | `INTERVALS_ENCRYPTION_KEY` |
| The athlete's own AI keys | `users.user_openai_api_key`, `.user_gemini_api_key` | `AI_KEY_ENCRYPTION_KEY` |
| Second factor | `users.totp_secret` | `TOTP_ENCRYPTION_KEY` |
| any of the above, unset | — | `SECRETS_ENCRYPTION_KEY` |

One key covered all four, so rotating it was an all-or-nothing operation on
every athlete's credentials at once — and the reason to rotate is usually a
suspicion, which is the worst moment for that. Worse than the backlog recorded:
it listed three purposes, written before `totp_secret` became an encrypted
column with #688, so a rotation would also have locked every athlete out of
their authenticator.

**The shared key is still required and is still the fallback.** Absent, a
dedicated key means "this purpose uses the shared one", which is what lets the
split land one secret type at a time — no migration, no deploy that has to set
four keys together. The column type is a `MultiFernet` built from
`[dedicated, shared]`: it encrypts with the first and decrypts with either, so
a purpose that gains a key writes new rows under it while old rows stay
readable, and moves across as rows are rewritten.

That last part is uneven, and it is why the shared key cannot be retired by
configuration. A Strava token is rewritten on every refresh; a TOTP secret is
written once at enrollment and never again. Retiring `SECRETS_ENCRYPTION_KEY`
needs a re-encryption pass that does not exist yet.

Rotation of a dedicated key does *not* need one: prepend the new key and keep
the old in the chain, and both old and new rows read while writes move forward.

Two things are refused at boot rather than at the next incident:

- a key Fernet cannot use — and **named**, because a dedicated key that failed
  on first use would break one column family in a process that started cleanly
  while the other three purposes kept working;
- a dedicated key set to the same value as `SECRETS_ENCRYPTION_KEY`. It
  validates, encrypts, decrypts and shows a configured key on the dashboard,
  while rotating either value still takes every secret type with it.

> **Not `STRAVA_ENCRYPTION_KEY`.** That name is the pre-#613 alias for the
> *shared* key and is still honoured as one. Using it for the Strava purpose —
> as the backlog proposed — would have kept Strava tokens working on a
> deployment that still sets it, and sent intervals, AI and TOTP secrets to a
> `SECRETS_ENCRYPTION_KEY` such a deployment never set: plaintext, silently.
> That is #612, reintroduced by a rename. Hence `STRAVA_TOKEN_ENCRYPTION_KEY`.

**The backup consequence.** Encrypting these columns means a database dump is
not a backup — it is half of one, and the half that cannot be used alone. There
are now five keys to lose rather than one, plus `authelia/users_database.yml`,
which is where the passwords actually live when header auth is on. A restore
that gets the dump and not the keys *appears to succeed*: the column type logs a
warning and returns the raw ciphertext, so the app boots clean and hands blobs
to Strava. See [`runbook-restore.md`](runbook-restore.md), which is both the
procedure and the record of when it was last rehearsed (ai-trainer-ops#13).

### Container

The backend runs as a non-root user. `APP_UID` must match the owner of the
bind-mounted `authelia/` directory on the host, because a bind mount keeps the
host's ownership rather than the image's. The backend checks this at startup and
logs a warning if it cannot write there.

### Automated scanning

`.github/workflows/security.yml` runs on every pull request, on every push to
`develop` and weekly. The weekly run is there because a new advisory against
an unchanged lockfile would otherwise never surface.

| scan | what it reads |
|---|---|
| `pip-audit` | the backend's runtime dependencies as `uv.lock` resolves them |
| `npm audit` | the frontend's dependencies, runtime and dev counted separately |
| `semgrep` | the Python, TypeScript and React rulesets over `backend/` and `frontend/src/` |
| `trivy` | both built images, base image included |
| ZAP baseline | the running frontend image (nginx with its real config) and the backend. Passive only |

**It reports first and gates second.** A new scan exits 0 and its findings
go to the job summary and an artifact, because a gate that is red on day one
gets switched off. Once a scan's backlog is at zero, it becomes a gate.
**semgrep is a gate.** Mark a false positive in place with
`# nosemgrep: <rule id>` and give the reason on the line above. Never exclude
a path or a rule to make it pass. The others still report.
Tools are pinned by version, and images by tag and digest. Dependabot cannot
see images referenced in a workflow, so bumping them is manual.

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

### Why revocation counts rather than timestamps

The obvious design is a "sessions valid from" timestamp compared against the
token's `iat`. It does not survive contact with the resolution of `iat`, which
is one second. A token minted in the same second as a revocation has to be
either accepted — leaving the revocation a hole — or rejected, which can fail
the legitimate re-login that immediately follows. Both answers are wrong, and
which one bites you depends on sub-second timing.

An integer has no such edge: it changes or it does not. It needs no clock, no
skew allowance, and it reads back as "this account has been signed out
everywhere N times". The counter is not a secret — a forged token still has to
be signed — so it only has to *change*, never to be unguessable.

For the same reason revocation is not implemented as a deny list of `jti`
values: that needs a table, an expiry sweep, and a lookup per request, to buy
per-token revocation the app has no use for. The token already dies with the
tab.

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

### If a session is compromised

In rough order of reach, so stop at the first one that covers the case:

| Suspicion | Action |
|---|---|
| One athlete's token | admin panel, the revoke button on their row — or `POST /api/v1/admin/users/{id}/revoke-sessions` with an admin token. They can do it themselves from settings if they can still sign in. |
| An admin token | rotate `ADMIN_PASSWORD` in the ops `production` environment and deploy. Every issued admin token stops verifying, because they are bound to it (#704). |
| The signing key itself | rotate `JWT_SECRET`. Signs out every athlete and re-keys the captcha and TOTP-challenge HMACs, which derive from it. |

A password changed directly in `authelia/users_database.yml` invalidates
nothing on its own — the app's tokens know nothing about that file. Revoke the
athlete's sessions in the same sitting.

---

## Known limitations

Recorded because a maintainer needs them, and because they are visible in the
code anyway.

**Revocation is per account, not per session.** Closed in #704, but with
edges worth knowing. There is no "sign this one device out" — the counter is on
the user row, so every revocation takes all of them, the caller included.
Nothing revokes *automatically*: enrolling the second factor drops trusted
devices but leaves sessions alone, on the grounds that it would sign the
athlete out in the middle of securing their account, and the explicit button is
right there. And the token is still valid for seven days if nobody presses
anything.

**Revocation does not reach the Authelia header path.** `get_current_user`
authenticates on `Remote-*` headers before it looks at a token, so a session
arriving that way has no generation to check. It now needs a valid
proof-of-transit secret, and no router forwards the headers that would use it
(#696) — but "no generation to check" is a property of the branch rather than of
the routing, so it still needs its own answer before forward auth is switched on.
Pinned as a known gap in `test_remote_user_boundary.py` rather than left to be
rediscovered.

**Rate limits are per process.** Windows live in module memory, so with N
replicas each key gets N times the allowance. This degrades gracefully for the
AI limits, less so for the auth ones — a multi-replica deployment should treat a
shared counter as a prerequisite. See [`multi_replica.md`](multi_replica.md).

**The global request-size limit reads `Content-Length`.** A chunked request
carries none and slips past it. It is a backstop; the routes that actually read
a large body cap themselves while reading, and that is the guarantee.

**Registration is open.** Deliberate, and bounded by the proof-of-work and the
rate limit rather than closed. Email verification (#687) would tighten it.

**An account created before header auth was switched on cannot be used under
it.** Nothing consults `users.hashed_password` in Authelia mode — the password
lives in `users_database.yml` and such a row has no entry there — so the account
can neither log in nor, since the step-up check reads the same store, be deleted
by its owner. Pre-existing and not something deletion introduced; it belongs
with #14, which covers the same file conflict from the registration side.

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
| #700 | the deployment contract as a checked-in manifest |
| #704 | making an issued JWT revocable |
