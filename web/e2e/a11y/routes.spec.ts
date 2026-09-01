import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// SAD 11E.3: an automated axe scan on every route, zero serious or critical
// violations. The route list grows with the screen inventory of UX section 8;
// the scan itself does not change.
const ROUTES = ['/'];

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
