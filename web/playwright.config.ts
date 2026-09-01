import { defineConfig, devices } from '@playwright/test';

// Three projects, matching three separate stages of SAD 11H:
//   journeys  e2e, the four journeys of SAD 11F.2
//   a11y      axe on every route, zero serious or critical violations
//   visual    Storybook snapshots, diff gate
//
// They are separate projects rather than tags so that the pipeline can report
// which of the three failed without parsing test names.

const CONSOLE_URL = process.env.DRAUPNIR_CONSOLE_URL ?? 'http://127.0.0.1:5173';
const STORYBOOK_URL = process.env.DRAUPNIR_STORYBOOK_URL ?? 'http://127.0.0.1:6006';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],

  // Never rewrite a baseline implicitly: a diff is the gate. A baseline that
  // does not yet exist for this platform is recorded explicitly by the visual
  // spec, which annotates the file to commit.
  updateSnapshots: 'none',

  expect: {
    timeout: 10_000,
    toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: 'disabled' },
  },

  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'journeys',
      testMatch: /journeys\/.*\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], baseURL: CONSOLE_URL },
    },
    {
      name: 'a11y',
      testMatch: /a11y\/.*\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], baseURL: CONSOLE_URL },
    },
    {
      name: 'visual',
      testMatch: /visual\/.*\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], baseURL: STORYBOOK_URL },
    },
  ],

  webServer: process.env.DRAUPNIR_NO_WEBSERVER
    ? undefined
    : [
        {
          command: 'pnpm run dev',
          url: CONSOLE_URL,
          reuseExistingServer: !process.env.CI,
          timeout: 120_000,
        },
        {
          command: 'pnpm run storybook',
          url: STORYBOOK_URL,
          reuseExistingServer: !process.env.CI,
          timeout: 180_000,
        },
      ],
});
