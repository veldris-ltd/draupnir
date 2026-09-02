import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Frontend unit tests: component behaviour, not implementation (SAD 11E.3).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['{apps,packages,tests}/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['**/node_modules/**', '**/dist/**', '**/__fixtures__/**', 'e2e/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['{apps,packages}/*/src/**'],
    },
  },
});
