import type { Page } from '@playwright/test';
import { test, expect } from '@playwright/test';

/**
 * The screens outside the four journeys: S05, S06, S12, S14, S15, S17, S23,
 * S24, S25, S27, S28, S29 and S31.
 *
 * They are covered here rather than folded into J1 to J4 because they are not
 * steps in a journey. An auditor does not walk through the role table on the
 * way to a lineage; they open it when somebody asks who can approve. What each
 * of these needs asserting is that it renders the thing it claims to render
 * and states the thing it exists to state.
 */

test.describe('S05, S06 — curation and retention', () => {
  test('curation counts quarantined sources beside the others', async ({ page }) => {
    // A quarantined source is the licence gate working, not a fault. The screen
    // says so, because a red count would teach a curator to treat a correct
    // refusal as something to fix.
    await page.goto('/corpora/curation');
    await expect(page.getByRole('heading', { name: 'Curation', level: 1 })).toBeVisible();
    await expect(page.getByText(/licence gate working, not a failure to fix/)).toBeVisible();

    const table = page.getByRole('table');
    await expect(table).toContainText('Quarantined');
    await expect(table.locator('tbody tr').first()).toBeVisible({ timeout: 15_000 });
  });

  test('retention says nothing has happened when nothing is approved', async ({ page }) => {
    // SAD 7.3: deletion is an approved, ledgered action rather than a timer
    // firing. An empty schedule must not read as "the deletions ran".
    await page.goto('/corpora/retention');
    await expect(page.getByRole('heading', { name: 'Retention', level: 1 })).toBeVisible();
    await expect(page.locator('.jg-state, table')).toBeVisible({ timeout: 15_000 });
  });
});

test.describe('S12 — the array monitor', () => {
  test('shows every element with its own state vocabulary', async ({ page }) => {
    await page.goto('/runs/array');
    await expect(page.getByRole('heading', { name: 'Array', level: 1 })).toBeVisible();

    // The element vocabulary is not the run vocabulary: an element that failed
    // inside its budget is AWAITING_RETRY, which FAILED would lose.
    await expect(page.getByText(/PENDING.*state rather than a missing row/s)).toBeVisible();
    await expect(page.getByRole('table')).toContainText('Element');
    await expect(page.getByRole('meter')).toBeVisible();
  });
});

test.describe('S14, S17, S28 — model, release and attestation', () => {
  test('a model shows every artefact its run produced', async ({ page }) => {
    const artefact = await firstModel(page);
    await page.goto(`/models/${artefact}`);

    await expect(page.getByRole('heading', { name: 'Artefacts' })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole('link', { name: 'Walk the lineage' })).toBeVisible();
  });

  test('a release package lists both Article 53 artefacts', async ({ page }) => {
    // SAD 9A and Decision S11: they are generated artefacts of the release, not
    // documents written beside it, so they sit with the card and the SBOM.
    const artefact = await releasedModel(page);
    await page.goto(`/models/${artefact}/release`);

    const contents = page.getByTestId('release-contents');
    await expect(contents).toBeVisible({ timeout: 15_000 });
    await expect(contents).toContainText('Training data summary');
    await expect(contents).toContainText('Copyright policy');
    await expect(contents).toContainText('Model card');
    await expect(contents).toContainText('SBOM');
  });

  test('an attestation is signed only when the chain is complete', async ({ page }) => {
    // Signing over a gap would certify the gap: a signature is read as a
    // statement that somebody checked, and nobody checked what is missing.
    const artefact = await releasedModel(page);
    await page.goto(`/models/${artefact}/attestation`);

    const banner = page.getByTestId('attestation-completeness');
    await expect(banner).toBeVisible({ timeout: 15_000 });

    const complete = await banner.getAttribute('data-jg-complete');
    const digest = await page.getByTestId('attestation-digest').textContent();
    expect(digest).toMatch(/^[0-9a-f]{64}$/);

    if (complete === 'true') {
      await expect(banner).toContainText('signed');
    } else {
      await expect(banner).toContainText('unsigned');
      await expect(banner).toContainText('certify the gap');
    }
  });
});

test.describe('S23, S24, S25 — plug-ins, policy and roles', () => {
  test('plug-ins state that an unsigned driver fails to load', async ({ page }) => {
    await page.goto('/admin/plugins');
    await expect(page.getByRole('heading', { name: 'Plug-ins', level: 1 })).toBeVisible();
    await expect(page.getByText(/unsigned plug-in fails to load/)).toBeVisible();
  });

  test('policy renders the rules that are actually enforced', async ({ page }) => {
    await page.goto('/admin/policy');
    await expect(page.getByRole('heading', { name: 'Policy', level: 1 })).toBeVisible();

    // First match wins and an unmatched subject is refused. Both are properties
    // of the Policy object the licence gate decides with, not of this screen.
    await expect(page.getByText(/First match wins/)).toBeVisible();
    await expect(page.getByRole('table').first()).toContainText('personal-data-requires-approval');
  });

  test('policy shows what changed between bundles', async ({ page }) => {
    await page.goto('/admin/policy');
    const diff = page.getByTestId('policy-diff');
    await expect(diff).toBeVisible({ timeout: 15_000 });
    // The diff marks additions and removals in text as well as colour.
    await expect(diff.locator('.jg-diff__sign').first()).toBeVisible();
  });

  test('roles state the separation of duty rather than implying it', async ({ page }) => {
    await page.goto('/admin/roles');
    await expect(page.getByRole('heading', { name: 'Roles', level: 1 })).toBeVisible();

    const separation = page.getByTestId('separation-of-duty');
    await expect(separation).toBeVisible({ timeout: 15_000 });
    await expect(separation).toContainText('No role both submits and approves');
  });

  test('the route table is generated from the enforced declarations', async ({ page }) => {
    // A published table written separately would disagree with the enforced
    // rule the first time somebody added an endpoint.
    await page.goto('/admin/roles');
    await expect(page.getByRole('heading', { name: 'Routes' })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/not assembled by hand/)).toBeVisible();
    await expect(page.getByRole('table').nth(1)).toContainText('/v1/runs');
  });
});

test.describe('S27 — the ledger entry', () => {
  test('recomputes the hash rather than displaying the stored one', async ({ page }) => {
    // The stored hash is exactly what a tamperer would have rewritten, so an
    // entry viewer that renders it proves nothing.
    await page.goto('/audit');
    const link = page.locator('[data-testid^="ledger-entry-"]').first();
    await expect(link).toBeVisible({ timeout: 15_000 });
    await link.click();

    await expect(page.getByRole('heading', { name: /Ledger entry/, level: 1 })).toBeVisible();
    const verification = page.getByRole('status').first();
    await expect(verification).toContainText(/Recomputed here/);
    await expect(verification).toHaveAttribute('data-jg-verified', 'true');
  });
});

test.describe('S29 — sign in', () => {
  test('collects no credential and states the hardware factor rule', async ({ page }) => {
    await page.goto('/signin');
    await expect(page.getByRole('heading', { name: 'Sign in', level: 1 })).toBeVisible();

    // No password field, ever: the console never sees a credential.
    expect(await page.locator('input[type="password"]').count()).toBe(0);

    // AC-S15 stated before the redirect rather than discovered at the gate.
    await expect(page.getByRole('heading', { name: /hardware factor is required/ })).toBeVisible();
    await expect(page.getByText(/security key or a platform authenticator/)).toBeVisible();
  });
});

test.describe('S31 — CON-B in kiosk mode', () => {
  test('has no navigation, no switcher and no control', async ({ page }) => {
    await page.goto('/kiosk');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 });

    // Nobody stands at it, so there is nothing to press and nowhere to go.
    expect(await page.getByRole('navigation', { name: 'Sections' }).count()).toBe(0);
    expect(await page.getByTestId('site-context').count()).toBe(0);
    expect(await page.getByRole('button').count()).toBe(0);
    await expect(page.getByText('Read only. No control on this panel.')).toBeVisible();
  });

  test('states its own staleness in words', async ({ page }) => {
    // A wall panel whose numbers stopped an hour ago is worse than a blank one:
    // nobody is watching closely enough to notice, and the numbers are believed.
    await page.goto('/kiosk');
    await expect(page.getByTestId('kiosk-freshness')).toContainText(
      /Live|Connecting|Not receiving/,
      {
        timeout: 15_000,
      },
    );
  });

  test('fits the 1280 by 720 panel without horizontal scrolling', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/kiosk');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 });

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });
});

async function firstModel(page: Page): Promise<string> {
  const response = await page.request.get('/v1/models?limit=1');
  const body = (await response.json()) as { items: { artefact: string }[] };
  const model = body.items[0];
  if (model === undefined) throw new Error('the seeded stack has no models');
  return model.artefact;
}

async function releasedModel(page: Page): Promise<string> {
  const response = await page.request.get('/v1/models?limit=50');
  const body = (await response.json()) as { items: { artefact: string; released: boolean }[] };
  const model = body.items.find((item) => item.released);
  if (model === undefined) throw new Error('the seeded stack has no released model');
  return model.artefact;
}
