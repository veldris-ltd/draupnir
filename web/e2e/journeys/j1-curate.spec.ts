import { test, expect } from '@playwright/test';

/**
 * J1 Curate — SAD 11F.2 / UX 10.1, acceptance evidence for AC-U1.
 *
 * "Corpora, register source, declare licence and attribution, answer the
 * personal data question, supply a DPIA reference where required, set any
 * residency constraint, ingest and hash, curate."
 *
 * The data protection gate is the step worth testing hardest. Legal corpora
 * are dense with named individuals, so it applies to most jurisdictions rather
 * than a minority, and a wizard that let a curator past it without a DPIA
 * reference would be a wizard that produced an unlawful corpus quietly.
 */

test.describe('J1 Curate', () => {
  test('register a source, through the data protection gate', async ({ page }) => {
    await page.goto('/corpora');
    await expect(page.getByRole('heading', { name: 'Corpora', level: 1 })).toBeVisible();

    // The register states what it holds, including the licence and whether
    // personal data was declared, because that is what a curator checks first.
    await expect(page.getByRole('table')).toContainText('OGL-UK-3.0');

    await page.getByRole('link', { name: 'Register a source' }).click();
    await expect(page.getByRole('heading', { name: 'Register a source', level: 1 })).toBeVisible();

    // -- step 1, the source --------------------------------------------------
    await page.getByLabel('Source URL').fill('https://www.legislation.gov.uk/uksi');
    await page.getByLabel('Licence (SPDX)').fill('OGL-UK-3.0');
    // The register records the digest of what the curator retrieved.
    // `retrievedAt` is required beside it, so the source has been fetched
    // and its digest exists; ingest hashes the corpus built from it, later.
    await page.getByRole('textbox', { name: /Content digest/ }).fill('a'.repeat(64));
    await page.getByRole('button', { name: 'Continue' }).click();

    // -- step 2, the data protection gate ------------------------------------
    await page.getByLabel('This source contains personal data').check();

    const panel = page.getByTestId('dpia-panel');
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('dense with named individuals');

    // The gate holds: Continue is unavailable until a reference is supplied.
    const advance = page.getByRole('button', { name: 'Continue' });
    await expect(advance).toBeDisabled();

    // By role, not by label: the panel's own heading is "A DPIA reference is
    // required", so it is labelled with a string that contains the field's
    // label and `getByLabel` matches both.
    await page.getByRole('textbox', { name: /DPIA reference/ }).fill('DPIA-2026-031');
    await expect(advance).toBeEnabled();
    await advance.click();

    // -- step 3, residency ---------------------------------------------------
    await page.getByLabel('Residency constraint').fill('sindri');
    await page.getByRole('button', { name: 'Continue' }).click();

    // -- step 4, review then register ----------------------------------------
    const review = page.getByTestId('register-review');
    await expect(review).toContainText('DPIA-2026-031');
    await expect(review).toContainText('sindri');

    await page.getByRole('button', { name: 'Register and ingest' }).click();
    await expect(page.getByTestId('registered-id')).toBeVisible({ timeout: 15_000 });
  });

  test('the gate cannot be walked past by going back and forward', async ({ page }) => {
    // The failure a wizard invites: declare personal data, retreat a step, and
    // return to find the gate satisfied by nothing.
    await page.goto('/corpora/register');
    await page.getByLabel('Source URL').fill('https://caselaw.nationalarchives.gov.uk');
    await page.getByRole('textbox', { name: /Content digest/ }).fill('b'.repeat(64));
    await page.getByRole('button', { name: 'Continue' }).click();

    await page.getByLabel('This source contains personal data').check();
    await page.getByRole('button', { name: 'Back' }).click();
    await page.getByRole('button', { name: 'Continue' }).click();

    await expect(page.getByTestId('dpia-panel')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled();
  });

  test('the register states which site it is showing', async ({ page }) => {
    // AC-U11, on a screen that is not the run board.
    await page.goto('/corpora');
    await expect(page.getByTestId('site-context')).toBeVisible();
  });
});
