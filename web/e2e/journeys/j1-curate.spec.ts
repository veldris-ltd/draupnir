import { test, expect } from '@playwright/test';

// J1 Curate -- SAD 11F.2 / UX 10.1.
//
// The specification exists now, ahead of the screens, because an empty harness
// that runs is worth more than a good harness added at the end. The steps are
// marked `fixme` rather than left to fail: a permanently red pipeline teaches
// people to ignore it, and AC-Q1 requires every stage to run on main.
// Prompt UX-3 replaces the body and removes the marker.
test.fixme('J1 Curate: register a source, clear licence, curate a corpus', async ({ page }) => {
  await page.goto('/sources/new');
  await expect(page.getByRole('heading', { name: 'Register source' })).toBeVisible();
});
