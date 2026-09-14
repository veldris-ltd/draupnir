import { readFile } from 'node:fs/promises';
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

  test('approves a deletion that is due, as a recorded decision', async ({ page }) => {
    // RF-27. The confirmation closed its dialog and did nothing else, on the
    // one action in the console that cannot be undone.
    await page.goto('/corpora/retention');
    const table = page.getByRole('table');
    await expect(table).toContainText('GBR raw corpus', { timeout: 15_000 });

    const offered = page.getByRole('button', { name: 'Approve deletion', disabled: false });
    if ((await offered.count()) === 0) {
      // The seeded proposal is consumed by the first run against a stack, and
      // the database outlives the run locally. What must then be true is that
      // the approval was recorded, not that it can be recorded twice. The
      // approval column names the approver, so what is asserted is that it no
      // longer says nobody has, and that the action is not offered again.
      // (It asserted the word "approver", which the column never renders, and
      // failed on every run after the first.)
      await expect(table).not.toContainText('not approved');
      await expect(page.getByRole('button', { name: 'Approve deletion' })).toHaveAttribute(
        'aria-disabled',
        'true',
      );
      return;
    }

    await offered.first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('cannot be recovered');

    const approved = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname.endsWith('/approve'),
    );
    await dialog.getByRole('button', { name: 'Approve the deletion' }).click();

    expect((await approved).status()).toBe(200);
    // Approved rather than deleted: the retention duty carries it out.
    await expect(page.getByTestId('retention-result')).toContainText('approved');
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

  test('requeues one element that stopped short, and offers it on no other', async ({ page }) => {
    // RF-27. `requeueArrayElement` existed from RF-13 and this screen had no
    // control for it, so S12's primary action could not be performed from S12.
    await page.goto('/runs/array');
    const table = page.getByRole('table');
    await expect(table).toContainText('EXHAUSTED', { timeout: 15_000 });

    // The seeded array has one element each AWAITING_RETRY, EXHAUSTED and
    // FAILED, beside ten completed and three running. Only the three carry the
    // control: a completed element requeued is an adapter trained twice.
    await expect(page.getByRole('button', { name: /^Requeue element \d+$/ })).toHaveCount(3);

    await page.getByRole('button', { name: 'Requeue element 15' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('are not touched');

    const requeued = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname.endsWith('/elements/15/requeue'),
    );
    await dialog.getByRole('button', { name: 'Requeue the element' }).click();

    expect((await requeued).status()).toBe(202);
    // Accepted rather than done: the worker performs it and records the
    // outcome, which at Sindri is a refusal naming what to run on REGIN.
    await expect(page.getByTestId('requeue-result')).toContainText('accepted');
  });
});

test.describe('S15 — the sweep comparison', () => {
  test('chooses a merge point that clears every gate, and no other', async ({ page }) => {
    // RF-27. The screen compared five points invented from one run's gate
    // values and reported one of them as selected. It shows the sweep the
    // worker merged and re-gated, and the choice among passing points is an
    // operator's.
    const runId = await evaluatedSweep(page);
    await page.goto(`/runs/${runId}/sweep`);
    await expect(page.getByTestId('sweep-trade')).toBeVisible({ timeout: 15_000 });

    const choices = page.getByTestId('sweep-choices');
    if ((await choices.count()) === 0) {
      // The seeded sweep is chosen by the first run against a stack, and the
      // database outlives the run locally. What must then be true is that the
      // choice is on the record, not that it can be made twice.
      await expect(page.getByTestId('sweep-selected')).toBeVisible();
      return;
    }

    // A point that fails a blocking gate is offered no choice. RAUN decides.
    await expect(choices.getByRole('button', { disabled: true })).not.toHaveCount(0);

    await choices.getByRole('button', { disabled: false }).first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('quantised from this point');

    const chosen = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname.endsWith('/select'),
    );
    await dialog.getByRole('button', { name: 'Choose this point' }).click();

    expect((await chosen).status()).toBe(200);
    await expect(page.getByTestId('sweep-result')).toContainText('chosen');
  });
});

async function evaluatedSweep(page: Page): Promise<string> {
  const response = await page.request.get('/v1/runs?state=MERGED&limit=50');
  const body = (await response.json()) as { items: { id: string }[] };
  for (const run of body.items) {
    const sweep = await page.request.get(`/v1/sweeps/${run.id}`);
    const read = (await sweep.json()) as { evaluated?: boolean };
    if (read.evaluated === true) return run.id;
  }
  throw new Error('the seeded stack has no merged run with an evaluated sweep');
}

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

  test('a document of the package downloads as the file it names', async ({ page }) => {
    // RF-27. S17 printed five addresses that nothing served. Each document is
    // now a download, generated from the release record and dated by the
    // release, so the same download is the same bytes.
    const artefact = await releasedModel(page);
    await page.goto(`/models/${artefact}/release`);
    await expect(page.getByTestId('release-contents')).toBeVisible({ timeout: 15_000 });

    const download = page.waitForEvent('download');
    await page.getByTestId('download-model-card').click();
    const file = await download;

    expect(file.suggestedFilename()).toMatch(/model-card\.md$/);
    const text = await readFile(await file.path(), 'utf-8');
    expect(text).toMatch(/^# /);
    // AC-S15: the sole approver exception is a disclosed fact on the card.
    expect(text).toContain('soleApproverException');
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

  // The two tests below are the only ones in this file that intercept a
  // response, and the reason is that the collector is not part of the seeded
  // stack. Everything else here reads the database, which the seed fills;
  // thermal comes from the DCGM exporter on an appliance, through Prometheus
  // on REGIN, and neither exists in CI. Interception is how the panel's two
  // states get exercised without inventing a fake estate.

  test('renders the temperature the collector reported', async ({ page }) => {
    await page.route('**/v1/estate/telemetry', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          readAt: '2026-09-08T12:00:00+00:00',
          source: 'http://regin.sindri.veldris.internal:9090',
          readings: [
            { metric: 'gpu_temperature', subject: 'dvalin', unit: 'C', value: 63.5, reason: null },
            {
              metric: 'throttle_reasons',
              subject: 'dvalin',
              unit: 'bitmask',
              value: 0,
              reason: null,
            },
          ],
        }),
      });
    });

    await page.goto('/kiosk');
    const tile = page.getByTestId('thermal-dvalin');
    await expect(tile).toBeVisible({ timeout: 15_000 });

    // A temperature, from the estate's own collector. Before RF-E15 this panel
    // showed 'under load' or 'idle' derived from run state, which reads 'idle'
    // in grey for an appliance that is idle *because* it is thermally
    // throttled -- the one case the panel exists for.
    await expect(tile).toContainText('63.5');
    await expect(tile).toContainText('not throttling');

    // A throttle bitmask of zero is a real reading, so it must not render as
    // an absence.
    expect(await tile.getByTestId('unmeasured').count()).toBe(0);
  });

  test('says unmeasured, and why, rather than showing a zero', async ({ page }) => {
    await page.route('**/v1/estate/telemetry', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          readAt: '2026-09-08T12:00:00+00:00',
          source: 'http://regin.sindri.veldris.internal:9090',
          readings: [
            {
              metric: 'gpu_temperature',
              subject: 'dvalin',
              unit: 'C',
              value: null,
              reason: 'Prometheus reports no DCGM_FI_DEV_GPU_TEMP for dvalin.',
            },
          ],
        }),
      });
    });

    await page.goto('/kiosk');
    const tile = page.getByTestId('thermal-dvalin');
    await expect(tile).toBeVisible({ timeout: 15_000 });

    // A panel showing 0 °C in green is worse than one saying it does not know:
    // nobody stands close enough to a wall panel to question a number, so the
    // fabricated one is simply believed.
    await expect(tile).toContainText('unmeasured');
    await expect(tile).toContainText('Prometheus reports no DCGM_FI_DEV_GPU_TEMP for dvalin.');
    expect(await tile.locator('[data-measured="true"]').count()).toBe(0);
  });

  // RF-28. The fabric dashboard, reached by its link rather than by waiting
  // for the rotation, shows the bandwidth against the commissioned baseline.

  test('shows the fabric bandwidth as a share of its baseline', async ({ page }) => {
    await fabricReadings(page, 212.0, 235.6);

    await page.goto('/kiosk?dashboard=fabric');
    const fraction = page.getByTestId('kiosk-fraction');
    await expect(fraction).toBeVisible({ timeout: 15_000 });

    await expect(page.getByTestId('kiosk-bandwidth')).toContainText('212');
    await expect(page.getByTestId('kiosk-baseline')).toContainText('235.6');
    await expect(fraction).toHaveText('90 per cent of baseline');
  });

  test('says when the fabric is below the 80 per cent floor', async ({ page }) => {
    await fabricReadings(page, 172.1, 235.6);

    await page.goto('/kiosk?dashboard=fabric');
    const fraction = page.getByTestId('kiosk-fraction');
    await expect(fraction).toBeVisible({ timeout: 15_000 });

    // SAD 11.3's alarm, stated in words: a colour alone is not read from
    // across a room, and is not read at all by anyone who cannot see it.
    await expect(fraction).toHaveText('73 per cent of baseline, below the 80 per cent floor');
  });

  test('holds a linked dashboard rather than rotating away from it', async ({ page }) => {
    await fabricReadings(page, 212.0, 235.6);
    await page.clock.install();

    await page.goto('/kiosk?dashboard=fabric');
    await expect(page.getByTestId('kiosk-fraction')).toBeVisible({ timeout: 15_000 });

    await page.clock.runFor(120_000);
    await expect(page.getByTestId('kiosk-fraction')).toBeVisible();
  });

  test('says the alarm cannot be judged when no baseline is recorded', async ({ page }) => {
    await fabricReadings(page, 212.0, null);

    await page.goto('/kiosk?dashboard=fabric');
    const note = page.getByTestId('kiosk-baseline-unmeasured');
    await expect(note).toBeVisible({ timeout: 15_000 });

    await expect(note).toContainText('DRAUPNIR_FABRIC_BASELINE_GBPS');
    expect(await page.getByTestId('kiosk-fraction').count()).toBe(0);
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

/** Answer the estate read with a fabric bandwidth and, if given, its baseline. */
async function fabricReadings(
  page: Page,
  bandwidth: number,
  baseline: number | null,
): Promise<void> {
  await page.route('**/v1/estate/telemetry', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        readAt: '2026-09-08T12:00:00+00:00',
        source: 'http://regin.sindri.veldris.internal:9090',
        readings: [
          {
            metric: 'fabric_bandwidth',
            subject: 'baugr',
            unit: 'GB/s',
            value: bandwidth,
            reason: null,
          },
          {
            metric: 'fabric_baseline',
            subject: 'baugr',
            unit: 'GB/s',
            value: baseline,
            reason:
              baseline === null
                ? 'Prometheus holds no commissioned baseline for the fabric probe. The worker ' +
                  'reports the one configured as DRAUPNIR_FABRIC_BASELINE_GBPS.'
                : null,
          },
        ],
      }),
    });
  });
}

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

test.describe('RF-03 — the sign-in button reaches something', () => {
  test('activating "Continue to MEGINGJORD" is not a 404', async ({ page }) => {
    // `SignIn.tsx` has navigated to `/auth/login?return_to=…` since it was
    // written, and that path existed nowhere: not as an API route, not in the
    // nginx configuration. The only button on the sign-in screen was a dead
    // link. This asserts it now reaches an endpoint.
    await page.goto('/signin');

    const response = await page.request.get('/auth/login?return_to=%2Fruns', {
      maxRedirects: 0,
    });

    expect(response.status()).not.toBe(404);
    // 303 to the identity provider, or 303 back to /signin when none is
    // configured. Either is an endpoint that exists and decided something.
    expect(response.status()).toBe(303);
    expect(response.headers().location).toBeTruthy();
  });

  test('the sign-in button is wired to that endpoint', async ({ page }) => {
    await page.goto('/signin');

    const button = page.getByRole('button', { name: /Continue to MEGINGJORD/i });
    await expect(button).toBeVisible({ timeout: 15_000 });

    // Navigation rather than a fetch: an OIDC flow is a browser redirect and
    // an XHR cannot complete one.
    await button.click();
    await page.waitForURL((url) => !url.pathname.startsWith('/signin') || url.search !== '', {
      timeout: 15_000,
    });
  });
});
