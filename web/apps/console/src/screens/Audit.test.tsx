import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PublishPanel } from './Audit';

/**
 * The publish control, against the site's standing with MEGINGJORD (G8).
 *
 * VLD-SAD-MEGINGJORD-001 AC-X6: a site MEGINGJORD has quarantined cannot obtain
 * a receipt, so its release blocks, and the console says so with MEGINGJORD's
 * reason. Disabled with the reason, as AC-U12 asks, rather than left available
 * to fail on the click.
 */

const REASON =
  'MEGINGJORD refused (site-not-anchorable): the site is QUARANTINED: a second history for seq 5';

function answer(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    headers: new Headers(),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response;
}

function standing(anchorState: string, anchorReason: string | null): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : (input as URL).href;
      if (url.includes('/healthz')) {
        return Promise.resolve(answer({ status: 'ok', version: '0.1.0', siteId: 'sindri' }));
      }
      return Promise.resolve(
        answer({
          items: [
            {
              id: 'sindri',
              name: 'Sindri',
              location: 'Nuneaton, United Kingdom',
              timezone: 'Europe/London',
              controlPlaneUri: 'https://alviss.sindri.veldris.internal',
              anchorState,
              lastAnchoredAt: '2026-10-12T09:00:00Z',
              anchorReason,
            },
          ],
        }),
      );
    }),
  );
}

describe('the publish control', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('is disabled for a quarantined site, with MEGINGJORD’s reason', async () => {
    standing('QUARANTINED', REASON);
    render(<PublishPanel artefact={'a'.repeat(64)} etag='"t"' />);

    const notice = await screen.findByTestId('publish-blocked');
    expect(notice.textContent).toContain('Sindri is quarantined by MEGINGJORD.');
    expect(notice.textContent).toContain('cannot obtain a receipt');
    expect(notice.textContent).toContain(REASON);
    // `aria-disabled`, which keeps it in the tab ring with its reason (AC-U12).
    const button = screen.getByRole('button', { name: /publish to registry/i });
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).toHaveAccessibleDescription(/MEGINGJORD has quarantined this site/);
  });

  it('is disabled for a site whose chain MEGINGJORD disagrees with', async () => {
    standing('DIVERGED', 'MEGINGJORD refused (fork-detected): seq 5 already anchored');
    render(<PublishPanel artefact={'a'.repeat(64)} etag='"t"' />);

    const notice = await screen.findByTestId('publish-blocked');
    expect(notice.textContent).toContain('disagrees with MEGINGJORD');
    expect(notice.textContent).toContain('fork-detected');
    expect(screen.getByRole('button', { name: /publish to registry/i })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('is available to an anchored site', async () => {
    standing('ANCHORED', null);
    render(<PublishPanel artefact={'a'.repeat(64)} etag='"t"' />);

    const button = await screen.findByRole('button', { name: /publish to registry/i });
    expect(button).not.toHaveAttribute('aria-disabled');
    expect(screen.queryByTestId('publish-blocked')).toBeNull();
  });
});
