# Technical TODO / Backlog

## Security

- **Separate encryption key per secret type** — `SECRETS_ENCRYPTION_KEY` (renamed from
  `STRAVA_ENCRYPTION_KEY` in #613) is the single Fernet key used to encrypt Strava OAuth
  tokens, Intervals.icu API keys, *and* user-supplied AI provider keys.  Key rotation
  therefore requires re-encrypting all three in one operation.  Consider splitting into
  per-purpose keys (`STRAVA_ENCRYPTION_KEY`, `INTERVALS_ENCRYPTION_KEY`,
  `AI_KEY_ENCRYPTION_KEY`) so each can be rotated independently without touching the
  others.  `SECRETS_ENCRYPTION_KEY` then stays as the fallback for any purpose that has
  no dedicated key, so the split can land one secret type at a time.
