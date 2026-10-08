/**
 * The stubbed stack: `AI_STUB_PROVIDER=true` (ai-trainer-ops#46).
 *
 * For everything that has to get *past* plan generation — the dashboard, the
 * day view, Settings — all of which need an onboarded account, which needs a
 * plan, which until the stub existed needed a real provider key in CI.
 *
 * Separate from `playwright.config.ts` rather than a project inside it: a
 * project picks a browser and a viewport, not a server, and the stub is chosen
 * before the key checks, so one process cannot serve both scenarios. Its own
 * ports and its own database, so both stacks can run at once and neither
 * inherits the other's accounts.
 *
 * The stub is locked out of production twice over (`config.Settings` refuses to
 * construct outside a development environment, and `llm.stub_is_active` checks
 * again at the point of use), so `APP_ENV=test` below is not decoration — the
 * backend will not start without it.
 */

import { defineStack } from './e2e/stack'

export default defineStack({
  name: 'stubbed',
  testDir: './e2e/stubbed',
  backendPort: 18767,
  frontendPort: 4175,
  database: 'e2e-stub-test.db',
  backendEnv: {
    AI_STUB_PROVIDER: 'true',
    // No key, deliberately: the stub has to be reachable without one, which is
    // the whole point of it being chosen before the key checks.
    GEMINI_API_KEY: '',
    OPENAI_API_KEY: '',
  },
})
