import type { Page } from '@playwright/test';
import { test, expect } from '@playwright/test';

/**
 * J3 Approve — SAD 11F.2 / UX 10.3, acceptance evidence for AC-U1 and AC-U13.
 *
 * "Gate queue, open artefact, read all six gate results, see the sole approver
 * notice, decide."
 *
 * AC-U13 is the criterion this journey exists to establish: "The gate queue
 * displays the gate evidence and the sole approver notice before the decision
 * control, not after." The assertions below check both halves of "before" —
 * the document order, and the fact that the decision controls are unavailable
 * until the evidence has actually been on screen.
 *
 * This journey has no time target. UX 10.3: "deliberately not optimised for
 * speed."
 */

test.describe('J3 Approve', () => {
  test('the queue orders by waiting time and shows the gate summary', async ({ page }) => {
    await page.goto('/gates');
    await expect(page.getByRole('heading', { name: 'Gates', level: 1 })).toBeVisible();
    await expect(page.getByText('Ordered by waiting time')).toBeVisible();

    // Never a bare tick: the summary states how many gates were met.
    await expect(page.getByRole('table')).toContainText(/of \d+ met/);
  });

  test('the evidence and the sole approver notice come before the decision', async ({ page }) => {
    const gateId = await firstGateId(page);
    await page.goto(`/gates/${gateId}`);

    const evidence = page.getByTestId('gate-evidence');
    const notice = page.getByTestId('sole-approver-notice');

    await expect(evidence).toBeVisible({ timeout: 15_000 });
    await expect(notice).toBeVisible();

    // Document order: evidence, then notice, then the decision.
    const order = await page.evaluate(() => {
      const at = (id: string) =>
        document
          .querySelector(`[data-testid="${id}"]`)
          ?.compareDocumentPosition(document.querySelector('.cn-decision') as Node);
      return {
        evidenceBeforeDecision: at('gate-evidence'),
        noticeBeforeDecision: at('sole-approver-notice'),
      };
    });
    // Node.DOCUMENT_POSITION_FOLLOWING === 4: the decision follows both.
    expect(order.evidenceBeforeDecision).toBe(4);
    expect(order.noticeBeforeDecision).toBe(4);
  });

  /**
   * AC-S8 and AC-S19, as the prompt asks it: "the decision controls must not
   * be reachable without the evidence entering the viewport first."
   *
   * Document order is necessary and not sufficient. A decision control can
   * follow the evidence in the DOM and still be the only thing on screen --
   * an anchor jump, a sticky footer, a short viewport with the evidence
   * scrolled past. So this asserts geometry, in the viewport an approver
   * actually has, at the moment the page settles:
   *
   *   1. the evidence is in the viewport when the screen loads;
   *   2. the decision controls are not;
   *   3. the evidence's bottom edge is above the decision's top edge, so
   *      reaching the decision means the evidence has been scrolled through.
   *
   * The third is what makes it a rule about reading rather than about markup.
   */
  test('the decision is unreachable until the evidence has been in the viewport', async ({
    page,
  }) => {
    const gateId = await firstGateId(page);
    // A short viewport, because a tall one hides the failure this guards
    // against: on a laptop the evidence and the decision fit together and the
    // question never arises.
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto(`/gates/${gateId}`);

    const evidence = page.getByTestId('gate-evidence');
    await expect(evidence).toBeVisible({ timeout: 15_000 });

    // 1. The evidence is on screen without scrolling.
    expect(await evidence.isVisible()).toBe(true);
    const evidenceBox = await evidence.boundingBox();
    expect(evidenceBox, 'the evidence table has no box').not.toBeNull();
    expect(evidenceBox?.y ?? Infinity).toBeLessThan(720);

    const decision = page.locator('.cn-decision');
    await expect(decision).toBeAttached();
    const decisionBox = await decision.boundingBox();
    expect(decisionBox, 'the decision controls have no box').not.toBeNull();

    // 3. Every part of the evidence is above the decision.
    const evidenceBottom = (evidenceBox?.y ?? 0) + (evidenceBox?.height ?? 0);
    expect(
      decisionBox?.y ?? 0,
      'a decision control sits level with or above the evidence',
    ).toBeGreaterThanOrEqual(evidenceBottom);

    // 2. And the decision is below the fold, so it cannot be pressed before
    //    the evidence has been read past. This is the assertion the prompt
    //    asks for, and it is the one that fails if somebody adds a sticky
    //    action bar.
    expect(
      await decision.evaluate((node) => {
        const box = node.getBoundingClientRect();
        return box.top < window.innerHeight && box.bottom > 0;
      }),
      'the decision controls were in the viewport before the evidence was scrolled',
    ).toBe(false);
  });

  test('the notice states the exception is disclosed, not a fault', async ({ page }) => {
    const gateId = await firstGateId(page);
    await page.goto(`/gates/${gateId}`);

    const notice = page.getByTestId('sole-approver-notice');
    await expect(notice).toBeVisible({ timeout: 15_000 });
    await expect(notice).toContainText('Nothing is wrong');
    await expect(notice).toContainText('lineage attestation');
    // SAD 9.4 records the exception rather than blocking the action.
    await expect(notice).toContainText('rather than blocking the action');
  });

  test('the gate results carry value, baseline and margin', async ({ page }) => {
    // "Gate card always renders value, baseline, margin and result. Never a
    // bare tick." The margin is what tells an approver whether a result is
    // comfortable or one rerun away from failing.
    const gateId = await firstGateId(page);
    await page.goto(`/gates/${gateId}`);

    const evidence = page.getByTestId('gate-evidence');
    await expect(evidence).toBeVisible({ timeout: 15_000 });
    await expect(evidence).toContainText('at or above');

    // Case-insensitive, because the panel renders "Margin" as a column header
    // and "margin" in the sentence naming the tightest result. Both are the
    // house sentence case; the assertion is about the number being there, not
    // about which of the two the reader meets first.
    await expect(evidence).toContainText(/margin/i);

    // And the three numbers themselves, not merely the word. A header with an
    // empty column under it would satisfy the line above.
    await expect(evidence.getByRole('columnheader', { name: /value/i })).toBeVisible();
    await expect(evidence.getByRole('columnheader', { name: /baseline/i })).toBeVisible();
    await expect(evidence).toContainText(/[+−±]\d/);
  });

  test('signing is two step with the consequence in words', async ({ page }) => {
    // AC-U15, on the action that matters most.
    const gateId = await firstGateId(page);
    await page.goto(`/gates/${gateId}`);
    await expect(page.getByTestId('gate-evidence')).toBeVisible({ timeout: 15_000 });

    // The evidence has been on screen, so the control is available.
    const approve = page.getByRole('button', { name: 'Sign and approve' });
    await expect(approve).toBeEnabled();
    await approve.click();

    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('sole approver exception');
    await expect(dialog).toContainText('cannot be unsigned');
  });

  test('rejection quarantines rather than deletes', async ({ page }) => {
    const gateId = await firstGateId(page);
    await page.goto(`/gates/${gateId}`);
    await expect(page.getByTestId('gate-evidence')).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: 'Reject' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('quarantined rather than deleted');
    await expect(dialog).toContainText('reason is required');
  });
});

async function firstGateId(page: Page): Promise<string> {
  const response = await page.request.get('/v1/gates?limit=1&state=pending');
  const body = (await response.json()) as { items: { id: string }[] };
  const gate = body.items[0];
  if (gate === undefined) throw new Error('the seeded stack has nothing awaiting approval');
  return gate.id;
}
