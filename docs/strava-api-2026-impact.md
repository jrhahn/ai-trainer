# Strava API 2026 Changes: App Impact

Last reviewed: 2026-06-01

## Summary

Strava's June 2026 developer-program update is relevant to AI Trainer because the
app has a direct Strava integration and uses Strava-derived activity data as part
of AI coaching workflows.

Dashboard status checked on 2026-06-01:

- Developer Tier: Standard Tier
- Category: Performance
- Dashboard banner: Strava is updating API access to subscriber-only and prompts
  the developer to start a subscription to maintain access.

The current code does not appear to use the Strava Club endpoints or Segment
Explore endpoints that are scheduled for deprecation on 2026-09-01. The main
risks are developer-tier access, Strava's stance on third-party AI processing,
and the 2027 technical migration away from the old API base URL.

## Current App Usage

Observed integration points:

- `backend/routers/strava.py` builds the Strava OAuth URL, exchanges OAuth
  codes, refreshes tokens, lists athlete activities, starts historical imports,
  and disconnects Strava locally.
- `backend/services/strava_service.py` fetches activity detail and activity
  streams.
- `frontend/src/services/strava.ts` calls the backend Strava endpoints for
  authorization, activity loading, import progress, and disconnect.
- Strava activity summaries and computed stream analyses are passed into AI
  coaching prompts through `backend/services/ai_service.py` and
  `backend/services/prompts.py`.

Relevant Strava API calls currently used:

- `GET https://www.strava.com/api/v3/athlete/activities`
- `GET https://www.strava.com/api/v3/activities/{activity_id}`
- `GET https://www.strava.com/api/v3/activities/{activity_id}/streams`
- `POST https://www.strava.com/oauth/token`
- `GET https://www.strava.com/oauth/authorize`

## Impact Assessment

### Not Currently Affected

- Club endpoint deprecations do not appear to affect the app. No code usage was
  found for Club Activities, Club Administrators, or Club Members.
- Segment Explore restrictions do not appear to affect the app. No code usage was
  found for Segment Explore.
- Authorization bearer headers are already used for Strava API requests.

### Affected Or Needs Attention

- Standard Tier developer access now requires a Strava subscription. Existing
  Standard Tier developers are expected to need a subscription by 2026-06-30.
- Standard Tier self-service access supports small test groups up to 10 athletes.
  Scaling beyond that likely requires Strava review.
- The app sends Strava-derived data into third-party AI providers. This is the
  most important policy risk for a scaled product because Strava's developer FAQ
  warns that applications exposing athlete data to third-party AI tools may not
  qualify for higher access if the use conflicts with their terms.
- The app deletes local Strava tokens on disconnect but does not currently call
  Strava's revoke endpoint.
- The hard-coded Strava API base URL must be migrated before 2027-06-01. Strava's
  FAQ says the new base URL becomes available on 2027-01-04.

## Action Points

### Immediate

- Confirm the app's athlete capacity and rate limits in the Strava API Settings
  Dashboard.
- Start or confirm a Strava subscription for the developer account before
  2026-06-30 to maintain Standard Tier API access.
- Review the updated Strava API Agreement and API Policy specifically for the
  app's use of OpenAI/Gemini on Strava-derived athlete data.
- Decide whether the current product should remain a small Standard Tier tool or
  be prepared for Extended Access review.

### Short Term

- Document the data flow from Strava to AI providers: which fields are sent, why
  they are needed, retention behavior, and whether raw streams leave the backend.
- Minimize prompt payloads where possible. Prefer computed metrics and concise
  summaries over raw or highly granular activity data when the coaching outcome
  does not need the full payload.
- Update the disconnect flow to call Strava's OAuth revoke endpoint before
  deleting the local token.
- Add a configuration value for the Strava API base URL instead of using
  `https://www.strava.com/api/v3` inline.

### Before 2027-06-01

- Switch API calls from `https://www.strava.com/api/v3` to
  `https://www.api-v3.strava.com` after Strava makes the new base URL available.
- Keep tokens in request headers only. The current code already does this.
- Use `oauth/revoke` instead of any deprecated deauthorization endpoint. The app
  currently does not call `oauth/deauthorize`, so this is mostly a disconnect
  improvement.

## Product Notes

Strava's official MCP is read-only and subscriber-focused. It is useful as a
market signal, but it is not a drop-in replacement for this app's integration
because AI Trainer persists its own metrics, builds training plans, imports
history, and combines Strava data with app-specific coaching state.

The most likely strategic paths are:

- Keep the app personal or limited-beta, use Standard Tier, and make the data
  flow very explicit.
- Prepare for Extended Access by tightening privacy documentation, reducing AI
  prompt payloads, and proving the app complements Strava rather than acting as a
  generic data extraction layer.
- Add a non-Strava ingestion path, such as FIT upload or other data import, so
  core coaching value is not fully dependent on Strava API access.

## Source Links

- Strava MCP Connector:
  https://support.strava.com/hc/en-us/articles/46190267796237-Strava-MCP-Connector
- Strava API and MCP FAQ:
  https://support.strava.com/hc/en-us/articles/46297163108493-Strava-API-and-MCP-FAQ
- Strava Developer FAQ:
  https://communityhub.strava.com/developers-knowledge-base-14/strava-api-faq-12906
- Strava V3 API Changelog:
  https://developers.strava.com/docs/changelog/
- Strava Getting Started docs:
  https://developers.strava.com/docs/getting-started/
