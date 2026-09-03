import type { Page } from '@playwright/test';
import { test, expect } from '@playwright/test';

/**
 * J2 Operate — SAD 11F.2 / UX 10.2, and the acceptance evidence for AC-U1.
 *
 * "Runs, compose specification, dry run, submit, watch the board, and on
 * failure diagnose and retry."
 *
 * Also the evidence for AC-U4 and AC-N3 (a state change on the board within
 * five seconds by server-sent events, with no manual refresh and no full list
 * poll), AC-F14 (a dry run that consumes no allocation), AC-U10 (a 200,000
 * line log that does not degrade the browser) and AC-U15 (a destructive action
 * that is two step with the consequence in words).
 */

test.describe('J2 Operate', () => {
  test('compose, dry run, submit, and watch the board', async ({ page }) => {
    // -- compose ------------------------------------------------------------
    await page.goto('/runs/compose');
    await expect(page.getByRole('heading', { name: 'Compose a run', level: 1 })).toBeVisible();

    // The editor arrives with a valid specification, which is the shape an
    // operator edits rather than one they type from nothing.
    const editor = page.getByTestId('spec-editor');
    await expect(editor).toBeVisible();

    // -- dry run: the primary action, and it consumes nothing ---------------
    await page.getByRole('button', { name: 'Dry run' }).click();
    const plan = page.getByTestId('dry-run-result');
    await expect(plan).toBeVisible({ timeout: 15_000 });
    await expect(plan).toContainText('No allocation was consumed');

    // The identity the submission would use, shown before the submission.
    const plannedIdentity = await page.getByTestId('dry-run-identity').textContent();
    expect(plannedIdentity).toMatch(/^[0-9a-f]{64}$/);

    // -- submit -------------------------------------------------------------
    await page.getByRole('button', { name: 'Submit this run' }).click();
    const submittedIdentity = page.getByTestId('submitted-identity');
    await expect(submittedIdentity).toBeVisible({ timeout: 15_000 });

    // AC-F1, from the operator's side: the identity the dry run showed is the
    // identity the run was submitted under. Nothing about it depends on which
    // client submitted, because neither client computes it.
    await expect(submittedIdentity).toHaveText(plannedIdentity ?? '');
  });

  test('a submission without a dry run takes an extra confirmation', async ({ page }) => {
    // An allocation on this estate is the scarce resource, so submitting blind
    // is possible and is not the path of least resistance.
    await page.goto('/runs/compose');
    await page.getByRole('button', { name: 'Submit', exact: true }).click();

    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Submit without a dry run?');
    await expect(dialog).toContainText('allocation is the scarce resource');
  });

  test('the board reflects a new run within five seconds, without a refresh', async ({ page }) => {
    // AC-U4 and AC-N3. The board is opened first and never reloaded; the run
    // is submitted through the API in another context, and the row has to
    // appear by itself.
    await page.goto('/runs');
    await expect(page.getByRole('heading', { name: 'Runs', level: 1 })).toBeVisible();
    await expect(page.getByTestId('board-freshness')).toContainText(/Live/, { timeout: 15_000 });

    const name = `cim-gbr-v9.${String(Date.now() % 1000)}`;
    const started = Date.now();

    const response = await page.request.post('/v1/runs', {
      headers: { 'Idempotency-Key': `journey-${String(started)}` },
      data: { specification: specificationNamed(name) },
    });
    expect(response.status(), await response.text()).toBe(202);

    // Five seconds is the budget. Given ten here, and asserted against the
    // measured elapsed time below, so a pass at nine seconds is a failure.
    await expect(page.getByText(name)).toBeVisible({ timeout: 10_000 });
    expect(Date.now() - started).toBeLessThan(5_000);
  });

  test('the board does not poll the list', async ({ page }) => {
    // "No full list poll" is half of AC-U4 and is invisible in a screenshot.
    // The board is left open and every request it makes is counted.
    const listReads: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname === '/v1/runs' && request.method() === 'GET') listReads.push(url.href);
    });

    await page.goto('/runs');
    await expect(page.getByTestId('board-freshness')).toContainText(/Live/, { timeout: 15_000 });
    await expect(page.locator('table tbody tr').first()).toBeVisible();

    // Counted from *after* the board has settled. The mount itself reads the
    // list once, and React's StrictMode reads it twice in development; neither
    // is a poll. What "no full list poll" forbids is a read that keeps
    // happening, so that is what is measured.
    listReads.length = 0;
    await page.waitForTimeout(6_000);

    expect(listReads).toEqual([]);
  });

  test('a 200,000 line log scrolls without laying out 200,000 rows', async ({ page }) => {
    // AC-U10. Virtualisation is asserted by counting the rows in the DOM,
    // because "it felt fine" is not a measurement.
    const runId = await firstRunId(page);
    await page.goto(`/runs/${runId}?tab=logs`);

    const viewport = page.getByRole('log');
    await expect(viewport).toBeVisible({ timeout: 15_000 });
    await expect(viewport).toHaveAttribute('aria-label', /200,000 lines/);

    const rendered = await page.locator('.jg-log__line').count();
    expect(rendered).toBeGreaterThan(0);
    expect(rendered).toBeLessThan(200);

    await viewport.evaluate((node) => {
      node.scrollTop = 500_000;
    });
    expect(await page.locator('.jg-log__line').count()).toBeLessThan(200);
  });

  test('cancelling a run is two step with the consequence in words', async ({ page }) => {
    // AC-U15.
    const runId = await firstRunId(page);
    await page.goto(`/runs/${runId}`);

    await page.getByRole('button', { name: 'Cancel this run' }).click();
    const dialog = page.getByRole('dialog');

    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('cannot be resumed');
    await expect(dialog).toContainText('Work done so far is kept');
    // The consequence is stated before the confirmation is available, not in a
    // toast after it.
    await expect(dialog.getByRole('button', { name: 'Cancel the run' })).toBeVisible();
  });
});

async function firstRunId(page: Page): Promise<string> {
  const response = await page.request.get('/v1/runs?limit=1');
  const body = (await response.json()) as { items: { id: string }[] };
  const run = body.items[0];
  if (run === undefined) throw new Error('the seeded stack has no runs');
  return run.id;
}

function specificationNamed(name: string): Record<string, unknown> {
  return {
    apiVersion: 'draupnir/v1',
    kind: 'AdapterRun',
    metadata: { name, jurisdiction: 'GBR', tier: 'A' },
    spec: {
      base: {
        artefact: 'hodd://models/core/MIDGARD-CORE-QWEN36-35B-A3B-v1.0',
        expectSha256: 'a'.repeat(64),
      },
      dataset: {
        artefact: 'hodd://corpora/GBR/curated',
        expectSha256: 'b'.repeat(64),
        cutoffPercentile: 99,
      },
      train: {
        driver: 'hamarr.llamafactory/v1',
        method: 'lora',
        precision: 'bf16',
        params: { rank: 16, save_steps: 500 },
      },
      placement: { driver: 'motsognir.slurm/v1', partition: 'default', nodes: 1 },
      evaluate: { driver: 'raun.lmeval/v1', suites: ['legal-qa'], gates: ['E1'], baseline: null },
      release: { route: 'tier-a', formats: ['gguf'], approval: 'required' },
    },
  };
}
