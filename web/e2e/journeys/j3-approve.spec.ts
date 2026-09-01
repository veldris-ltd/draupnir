import { test, expect } from '@playwright/test';

// J3 Approve -- SAD 11F.2 / UX 10.3.
test.fixme('J3 Approve: review gates, sign off, publish a release', async ({ page }) => {
  await page.goto('/approvals');
  await expect(page.getByRole('heading', { name: 'Approval queue' })).toBeVisible();
});
