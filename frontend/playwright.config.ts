/**
 * The keyless stack: no provider key, no admin fallback (ai-trainer-ops#46).
 *
 * The default config, and the one most specs belong in. It is the state a fresh
 * deployment is in, and the only one in which the #41 dead end is reproducible
 * in a browser — plan generation answers 402 because there is nothing to call.
 *
 * The companion is `playwright.stub.config.ts`, which turns the stub on so a
 * dashboard with a plan can be reached. They cannot be one config: the stub is
 * chosen *before* the key checks, so enabling it here would silently delete
 * every assertion about the keyless path.
 */

import { defineStack } from './e2e/stack'

export default defineStack({
  name: 'keyless',
  testDir: './e2e/keyless',
  backendPort: 18766,
  frontendPort: 4174,
  database: 'e2e-test.db',
  backendEnv: {
    // The mode the landing page promises ("you bring a Gemini key"), and the
    // one that makes plan generation answer 402 rather than call a provider.
    ALLOW_ADMIN_AI_KEY_FALLBACK: 'false',
    GEMINI_API_KEY: '',
    OPENAI_API_KEY: '',
  },
})
