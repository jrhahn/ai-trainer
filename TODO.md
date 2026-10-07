# Technical TODO / Backlog

## Security

- **Re-encrypt a purpose's rows, so the shared key can be retired** — the per-purpose
  split landed in ai-trainer-ops#12 (`STRAVA_TOKEN_ENCRYPTION_KEY`,
  `INTERVALS_ENCRYPTION_KEY`, `AI_KEY_ENCRYPTION_KEY`, `TOTP_ENCRYPTION_KEY`, with
  `SECRETS_ENCRYPTION_KEY` as the fallback). Rotation of a dedicated key needs nothing
  more: keep the old key in the chain and writes move forward on their own.

  Retiring the shared key does. Rows move to a dedicated key only as they are next
  written, which is every refresh for a Strava token and effectively never for a TOTP
  secret — written once at enrollment. So `SECRETS_ENCRYPTION_KEY` stays required until
  something walks a purpose's columns and rewrites them. `MultiFernet.rotate` is the
  tool; the care is that it rewrites credentials in bulk and a mistake destroys them.

  Note for whoever writes it: the backlog said three secret types and there were four.
  `totp_secret` became an encrypted column with #688 and this note was not revisited.
  `test_secret_key_split.py` now fails if a fifth appears without a purpose.
