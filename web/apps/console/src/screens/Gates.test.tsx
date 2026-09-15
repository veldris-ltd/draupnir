import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApprovalDetail } from './Gates';
import { SIGNING_AGENT } from '../signing';

/**
 * RF-40, S13's half. An approval is signed by the approver's signing agent,
 * and an approver whose agent is not answering is told so on the screen, with
 * approval unavailable, rather than refused after confirming. The journey
 * confirms an approval end to end; what is asserted here is each state the
 * screen can be in before anybody presses anything.
 *
 * Where approval is unavailable, the reason is stated twice on purpose: in
 * the status line, and as the approve button's accessible description, so a
 * screen reader user who reaches the button hears why it does nothing.
 */

const GATE = '019cf270-ba80-76c9-84ca-7374e16c7631';

const QUEUE = {
  items: [
    {
      id: GATE,
      runId: GATE,
      model: 'cim-fji-v0.1',
      artefactSha256: '7'.repeat(64),
      gates: [],
      submittedBy: 'operator@veldris.internal',
      awaitingSince: '2026-09-15T08:00:00+00:00',
      retryCount: 0,
      etag: '"abc"',
      signing: {
        subject: GATE,
        approver: 'akuma',
        policyVersion: 'gleipnir/2026.01',
        soleApproverException: false,
      },
    },
  ],
  nextCursor: null,
  limit: 100,
};

function answer(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    headers: new Headers(),
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function stub(agent: () => Promise<Response>): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).href;
      if (url.startsWith(SIGNING_AGENT)) return agent();
      return Promise.resolve(answer(QUEUE));
    }),
  );
}

async function statusSays(text: RegExp): Promise<void> {
  const status = await screen.findByTestId('signing-agent');
  await waitFor(() => {
    expect(status).toHaveTextContent(text);
  });
}

function approveButton(): HTMLElement {
  return screen.getByRole('button', { name: /^Sign and approve/ });
}

describe('S13 before a decision', () => {
  beforeEach(() => {
    vi.stubGlobal('EventSource', undefined);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('says so, and offers no approval, when no signing agent answers', async () => {
    stub(() => Promise.reject(new TypeError('Failed to fetch')));
    render(<ApprovalDetail gateId={GATE} />);

    await statusSays(/No signing agent is answering/);
    expect(approveButton()).toHaveAttribute('aria-disabled', 'true');
    expect(approveButton()).toHaveAccessibleName(/No signing agent is answering/);
    expect(screen.getByRole('button', { name: 'Reject' })).not.toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('names the key it will sign with when the agent holds the approver’s', async () => {
    stub(() => Promise.resolve(answer({ approver: 'akuma', keyId: '0123456789abcdef' })));
    render(<ApprovalDetail gateId={GATE} />);

    await statusSays(/Signing as akuma with key 0123456789abcdef/);
    expect(approveButton()).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('says whose key the agent holds when it is not the approver’s', async () => {
    stub(() => Promise.resolve(answer({ approver: 'somebody-else', keyId: 'fedcba9876543210' })));
    render(<ApprovalDetail gateId={GATE} />);

    await statusSays(/holds somebody-else’s key/);
    expect(approveButton()).toHaveAttribute('aria-disabled', 'true');
    expect(approveButton()).toHaveAccessibleName(/holds somebody-else’s key/);
  });
});
