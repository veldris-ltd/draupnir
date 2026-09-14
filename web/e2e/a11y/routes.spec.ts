import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// SAD 11E.3: an automated axe scan on every route, zero serious or critical
// violations. The route list grows with the screen inventory of UX section 8;
// the scan itself does not change.
//
// Detail routes are included with a subject taken from the seeded stack, so
// the scan covers a screen with data on it rather than only its empty state
// -- a table with no rows has far fewer ways to be inaccessible than one
// with them.
const ROUTES = [
  '/',
  '/corpora',
  '/corpora/register',
  '/corpora/curation',
  '/corpora/retention',
  '/runs',
  '/runs/array',
  '/runs/compose',
  '/models',
  '/gates',
  '/audit',
  '/sites',
  '/admin/plugins',
  '/admin/policy',
  '/admin/roles',
  '/signin',
  '/kiosk',
];

for (const route of ROUTES) {
  test(`axe: ${route} has no serious or critical violation`, async ({ page }) => {
    await page.goto(route);
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();

    const blocking = results.violations.filter(
      (violation) => violation.impact === 'serious' || violation.impact === 'critical',
    );

    expect(
      blocking,
      blocking.map((v) => `${v.id}: ${v.help} (${String(v.nodes.length)} elements)`).join('\n'),
    ).toEqual([]);
  });
}

/**
 * The detail screens, scanned with a real subject.
 *
 * Resolved from the seeded stack at run time rather than hard-coded: a digest
 * written into a spec goes stale the first time the seed changes, and a scan
 * that silently ran against a 404 would report zero violations for the best
 * possible reason and the worst one.
 */
const DETAIL: readonly { name: string; path: (subject: string) => string; of: 'model' | 'run' }[] =
  [
    { name: 'model detail', path: (a) => `/models/${a}`, of: 'model' },
    { name: 'lineage explorer', path: (a) => `/models/${a}/lineage`, of: 'model' },
    { name: 'release package', path: (a) => `/models/${a}/release`, of: 'model' },
    { name: 'attestation export', path: (a) => `/models/${a}/attestation`, of: 'model' },
    { name: 'run detail', path: (r) => `/runs/${r}`, of: 'run' },
    { name: 'sweep comparison', path: (r) => `/runs/${r}/sweep`, of: 'run' },
  ];

for (const screen of DETAIL) {
  test(`axe: ${screen.name} has no serious or critical violation`, async ({ page }) => {
    const collection = screen.of === 'model' ? '/v1/models?limit=1' : '/v1/runs?limit=1';
    const response = await page.request.get(collection);
    const body = (await response.json()) as { items: Record<string, string>[] };
    const first = body.items[0];
    expect(first, `the seeded stack has no ${screen.of}`).toBeDefined();

    const subject = screen.of === 'model' ? first?.artefact : first?.id;
    await page.goto(screen.path(subject ?? ''));
    await page.waitForSelector('h1');

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();

    const blocking = results.violations.filter(
      (violation) => violation.impact === 'serious' || violation.impact === 'critical',
    );

    expect(
      blocking,
      blocking.map((v) => `${v.id}: ${v.help} (${String(v.nodes.length)} elements)`).join('\n'),
    ).toEqual([]);
  });
}

/**
 * CON-B's two measured dashboards, populated (RF-28).
 *
 * `/kiosk` above is scanned in the state the seeded stack gives it, which has
 * no collector: every tile says unmeasured. A tile with a temperature, a
 * throttle badge, a bandwidth and a percentage of baseline has more ways to be
 * inaccessible than one saying it does not know, so both are scanned with
 * readings in them. The readings are intercepted because the collector is
 * Prometheus on REGIN, which no CI runner has.
 */
const POPULATED = {
  readAt: '2026-09-08T12:00:00+00:00',
  source: 'http://regin.sindri.veldris.internal:9090',
  readings: [
    { metric: 'gpu_temperature', subject: 'dvalin', unit: 'C', value: 63.5, reason: null },
    { metric: 'throttle_reasons', subject: 'dvalin', unit: 'bitmask', value: 4, reason: null },
    { metric: 'fabric_bandwidth', subject: 'baugr', unit: 'GB/s', value: 172.1, reason: null },
    { metric: 'fabric_baseline', subject: 'baugr', unit: 'GB/s', value: 235.6, reason: null },
  ],
};

for (const dashboard of ['thermal', 'fabric'] as const) {
  test(`axe: kiosk ${dashboard} dashboard, populated, has no serious or critical violation`, async ({
    page,
  }) => {
    await page.route('**/v1/estate/telemetry', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(POPULATED),
      });
    });
    await page.goto(`/kiosk?dashboard=${dashboard}`);
    const tile =
      dashboard === 'thermal'
        ? page.getByTestId('thermal-dvalin')
        : page.getByTestId('kiosk-fraction');
    await expect(tile).toBeVisible({ timeout: 15_000 });

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    const blocking = results.violations.filter(
      (violation) => violation.impact === 'serious' || violation.impact === 'critical',
    );

    expect(
      blocking,
      blocking.map((v) => `${v.id}: ${v.help} (${String(v.nodes.length)} elements)`).join('\n'),
    ).toEqual([]);
  });
}
