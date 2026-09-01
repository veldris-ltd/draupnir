import { test, expect } from '@playwright/test';

// J4 Audit -- SAD 11F.2 / UX 10.4.
test.fixme('J4 Audit: trace a release back to every source through lineage', async ({ page }) => {
  await page.goto('/lineage');
  await expect(page.getByRole('heading', { name: 'Lineage explorer' })).toBeVisible();
});
