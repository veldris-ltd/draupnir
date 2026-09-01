import { test, expect } from '@playwright/test';

// J2 Operate -- SAD 11F.2 / UX 10.2. See j1-curate.spec.ts for why this is
// marked fixme rather than failing.
test.fixme('J2 Operate: submit a run, watch it train, diagnose a failure', async ({ page }) => {
  await page.goto('/runs');
  await expect(page.getByRole('heading', { name: 'Run board' })).toBeVisible();
});
