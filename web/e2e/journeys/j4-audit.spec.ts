import type { Page } from '@playwright/test';
import { test, expect } from '@playwright/test';

/**
 * J4 Audit — SAD 11F.2 / UX 10.4, acceptance evidence for AC-U1.
 *
 * "Models, select release, lineage explorer, walk to base licence and corpus
 * hashes, verify the ledger chain, export the attestation. A gap in the chain
 * is rendered explicitly."
 *
 * Target: "a complete lineage is reached in three interactions or fewer." That
 * is measured below by counting the interactions rather than by asserting the
 * screens exist in the right order, because three screens reachable in five
 * clicks would satisfy the letter and miss the point.
 */

test.describe('J4 Audit', () => {
  test('a lineage is reached in three interactions or fewer', async ({ page }) => {
    let interactions = 0;

    // 1. Models.
    await page.goto('/');
    await page.getByRole('link', { name: 'Models' }).click();
    interactions += 1;
    await expect(page.getByRole('heading', { name: 'Models', level: 1 })).toBeVisible();

    // 2. Select a release. The registry carries the artefact digest, which is
    //    the lineage key, so this is one click and not a click plus a lookup.
    const firstModel = page.locator('table tbody tr a').first();
    await expect(firstModel).toBeVisible({ timeout: 15_000 });
    await firstModel.click();
    interactions += 1;
    await expect(page.getByRole('heading', { name: 'Artefacts' })).toBeVisible({
      timeout: 15_000,
    });

    // 3. Walk the lineage. UX section 8 puts the model detail between the
    //    registry and the lineage — S13, S14, S16 — and S14's primary action
    //    is opening the lineage, so this is the intended path rather than a
    //    detour. Three interactions, which is the target.
    await page.getByRole('link', { name: 'Walk the lineage' }).click();
    interactions += 1;

    await expect(page.getByRole('heading', { name: 'Lineage', level: 1 })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId('lineage-completeness')).toBeVisible();

    expect(interactions).toBeLessThanOrEqual(3);
  });

  test('the lineage reaches licences and corpus hashes', async ({ page }) => {
    await gotoLineage(page);

    await expect(page.getByTestId('lineage-licences')).toBeVisible({ timeout: 15_000 });
    // Corpus hashes are the other end of the chain: an attestation that names
    // a licence without naming the bytes it applies to attests to nothing.
    const hashes = page.getByTestId('lineage-corpus-hashes');
    await expect(hashes).toBeVisible();
    await expect(hashes.locator('code').first()).toHaveText(/^[0-9a-f]{64}$/);
  });

  test('a gap is rendered as a marked node, never as a shorter tree', async ({ page }) => {
    // The UX specification is explicit: "A gap renders as a marked node
    // stating what is missing, never as a shorter tree." A chain that simply
    // stops looks complete to anyone who does not already know how long it
    // should be, which is everyone reading it for the first time.
    await gotoLineage(page);

    const banner = page.getByTestId('lineage-completeness');
    await expect(banner).toBeVisible({ timeout: 15_000 });

    const complete = await banner.getAttribute('data-jg-complete');
    if (complete === 'false') {
      // Every gap the API reported is listed, and each is a node in the tree
      // carrying what is missing.
      await expect(page.getByTestId('lineage-gaps')).toBeVisible();
      await expect(page.getByRole('tree')).toContainText('MISSING:');
    } else {
      await expect(banner).toContainText('no gaps');
    }
  });

  test('the ledger states whether the slice chains', async ({ page }) => {
    await page.goto('/audit');
    await expect(page.getByRole('heading', { name: 'Audit', level: 1 })).toBeVisible();

    const verification = page.getByTestId('chain-verification');
    await expect(verification).toBeVisible({ timeout: 15_000 });
    // The verification travels with the slice. An endpoint that returned
    // entries without saying whether they chain makes tampering look like
    // data.
    await expect(verification).toHaveAttribute('data-jg-verified', 'true');
    await expect(verification).toContainText('chains end to end');
  });

  test('the ledger shows entries with their hashes', async ({ page }) => {
    await page.goto('/audit');
    const table = page.getByRole('table');
    await expect(table).toBeVisible({ timeout: 15_000 });
    await expect(table).toContainText('Entry hash');
    await expect(table.locator('tbody tr').first()).toBeVisible();
  });

  test('a released model discloses a sole approver on the registry row', async ({ page }) => {
    // SAD 9.4: disclosed, not hidden. A disclosure that takes two clicks to
    // find is a disclosure in name only, so it is on the list.
    await page.goto('/models');
    await expect(page.getByRole('table')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('table')).toContainText('Approval');
  });
});

async function gotoLineage(page: Page): Promise<void> {
  const response = await page.request.get('/v1/models?limit=1');
  const body = (await response.json()) as { items: { artefact: string }[] };
  const model = body.items[0];
  if (model === undefined) throw new Error('the seeded stack has no models');
  await page.goto(`/models/${model.artefact}/lineage`);
}
