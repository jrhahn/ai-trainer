import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    // `e2e/**` is Playwright's, and vitest's default include matches
    // `*.spec.ts` anywhere — so without this it loads the smoke test and dies
    // on "Playwright Test did not expect test.describe() to be called here".
    // Verified both ways: removing this line makes `vitest run e2e` fail with
    // exactly that.
    exclude: ['**/node_modules/**', 'src/test/integration/**', 'e2e/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/main.tsx',
        'src/App.tsx',
        'src/test/**',
        'src/**/*.test.{ts,tsx}',
        'src/**/*.d.ts',
      ],
      // Coverage floor — fails `npm run test:coverage` (and CI) on regression.
      // Set just below the current levels as a ratchet; raise as coverage grows.
      thresholds: {
        lines: 89,
        statements: 85,
        functions: 86,
        branches: 75,
      },
    },
  },
})
