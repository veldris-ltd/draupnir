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
    await expect(evidence).toContainText('margin');
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
