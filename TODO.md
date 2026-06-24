# Technical TODO / Backlog

## Security

- **Separate encryption key per secret type** — currently `STRAVA_ENCRYPTION_KEY` is
  the single Fernet key used to encrypt Strava OAuth tokens, Intervals.icu API keys,
  *and* user-supplied AI provider keys.  Key rotation therefore requires re-encrypting
  all three in one operation.  Consider splitting into per-purpose keys
  (`STRAVA_ENCRYPTION_KEY`, `INTERVALS_ENCRYPTION_KEY`, `AI_KEY_ENCRYPTION_KEY`) so
  each can be rotated independently without touching the others.
