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
  // Two workers in CI; locally Playwright's own default, which is a
  // fraction of the core count. Spelled as a spread because
  // `exactOptionalPropertyTypes` will not accept `undefined` for "unset".
  ...(process.env.CI ? { workers: 2 } : {}),
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

  // Omitted rather than set to undefined: `exactOptionalPropertyTypes` makes
  // the difference real, and Playwright's own type does not admit undefined.
  ...(process.env.DRAUPNIR_NO_WEBSERVER
    ? {}
    : {
        webServer: [
          {
            command: 'pnpm run dev',
            url: CONSOLE_URL,
            reuseExistingServer: !process.env.CI,
            timeout: 120_000,
          },
          {
            // Built, then served statically. The dev server compiles on
            // demand, and parallel shards navigating every story through it
            // contend badly enough to look like a broken component rather
            // than a busy bundler. The static build is also the artefact the
            // snapshots should be taken against.
            command: 'pnpm run build-storybook && pnpm run storybook:serve',
            url: STORYBOOK_URL,
            reuseExistingServer: !process.env.CI,
            timeout: 300_000,
          },
        ],
      }),
});
