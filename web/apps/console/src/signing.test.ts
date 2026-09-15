import { afterEach, describe, expect, it, vi } from 'vitest';
import { SIGNING_AGENT, probeAgent, signApproval } from './signing';

/**
 * RF-40. The console asks the approver's signing agent who it is before
 * offering approval, and has it sign. What is asserted here is the console's
 * half: an absent agent is a stated condition rather than an exception, and a
 * refusal carries the agent's own words.
 */

function reply(status: number, body: unknown): Response {
  return {
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

const FIELDS = {
  subject: '019cf270-ba80-76c9-84ca-7374e16c7631',
  approver: 'akuma',
  policyVersion: 'gleipnir/2026.01',
  soleApproverException: true,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the signing agent, from the console', () => {
  it('is ready when it names the approver whose key it holds', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(reply(200, { approver: 'akuma', keyId: '0123456789abcdef' }))),
    );

    expect(await probeAgent()).toEqual({
      state: 'ready',
      identity: { approver: 'akuma', keyId: '0123456789abcdef' },
    });
  });

  it('is unreachable, naming where it looked, when nothing answers', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    );

    const state = await probeAgent();

    expect(state.state).toBe('unreachable');
    expect(state.state === 'unreachable' ? state.reason : '').toContain(SIGNING_AGENT);
  });

  it('sends the approval fields and returns the signature with its instant', async () => {
    const fetcher = vi.fn(() =>
      Promise.resolve(reply(200, { signature: 'ab12', decidedAt: '2026-09-15T09:00:00+00:00' })),
    );
    vi.stubGlobal('fetch', fetcher);

    const signed = await signApproval(FIELDS);

    expect(signed).toEqual({ signature: 'ab12', decidedAt: '2026-09-15T09:00:00+00:00' });
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${SIGNING_AGENT}/v1/sign-approval`);
    expect(JSON.parse(init.body as string)).toMatchObject({ ...FIELDS, decision: 'approved' });
  });

  it("carries the agent's refusal in its own words", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve(reply(422, { error: 'this agent signs only for its own approver' })),
      ),
    );

    await expect(signApproval(FIELDS)).rejects.toThrow('its own approver');
  });
});
