/**
 * Separate Vitest configuration for integration tests.
 *
 * Differences from the default vite.config.ts:
 *  - environment: 'node'  — no jsdom; uses Node.js native fetch for real HTTP calls
 *  - globalSetup          — starts/stops the FastAPI backend process
 *  - include              — only runs files under src/test/integration/
 *  - define               — sets VITE_BACKEND_URL to point at the test backend port
 *    (replaces the compile-time import.meta.env references in api.ts and auth.ts)
 *
 * Run with:  npm run test:integration
 */

import { defineConfig } from 'vitest/config'

const INTEGRATION_BACKEND_PORT = 18765

export default defineConfig({
  test: {
    environment: 'node',
    globalSetup: './src/test/integration/global-setup.ts',
    include: ['src/test/integration/**/*.test.ts'],
    globals: true,
    // Give each test enough time for a real HTTP round-trip
    testTimeout: 15000,
    hookTimeout: 60000,
  },
  define: {
    // These compile-time replacements make api.ts point at the integration backend.
    // Without them, api.ts falls back to its default of http://localhost:8000.
    'import.meta.env.VITE_BACKEND_URL': JSON.stringify(`http://127.0.0.1:${INTEGRATION_BACKEND_PORT}`),
    'import.meta.env.VITE_AUTHELIA_URL': JSON.stringify(''),
  },
})
