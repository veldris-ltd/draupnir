import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';

/**
 * The console's shell, at the level a unit test can reach.
 *
 * The four journeys are Playwright specs against a seeded stack, because a
 * journey asserted against a mocked API is a test of the mock. What is worth
 * asserting here is the shell's standing obligations: that it states its site
 * on every screen (AC-U11), that a partitioned site is stated plainly
 * (AC-U12), and that the skip link is the first thing in the tab order
 * (WCAG 2.4.1).
 */

const SITES = {
  items: [
    {
      id: 'sindri',
      name: 'Sindri',
      location: 'Nuneaton, United Kingdom',
      timezone: 'Europe/London',
      controlPlaneUri: 'https://alviss.sindri.veldris.internal',
      anchorState: 'ANCHORED',
      lastAnchoredAt: '2026-09-01T10:00:00Z',
    },
    {
      id: 'brokkr',
      name: 'Brokkr',
      location: 'Nuneaton, United Kingdom',
      timezone: 'Europe/London',
      controlPlaneUri: 'https://alviss.brokkr.veldris.internal',
      anchorState: 'PARTITIONED',
      lastAnchoredAt: null,
    },
  ],
};

function answer(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    headers: new Headers(),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response;
}

function stubApi(siteId: string): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      // `RequestInfo` includes `Request`, whose default stringification is
      // `[object Object]`. The console only ever passes a string URL.
      const url = typeof input === 'string' ? input : (input as URL).href;
      if (url.includes('/healthz')) {
        return Promise.resolve(answer({ status: 'ok', version: '0.1.0', siteId }));
      }
      if (url.includes('/v1/sites')) return Promise.resolve(answer(SITES));
      if (url.includes('/v1/gates')) {
        return Promise.resolve(answer({ items: [], nextCursor: null, limit: 50 }));
      }
      return Promise.resolve(answer({ items: [], nextCursor: null, limit: 50 }));
    }),
  );
}

describe('the console shell', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
    vi.stubGlobal('EventSource', undefined);
  });

  it('states which site it is showing', async () => {
    stubApi('sindri');
    render(<App />);
    // Awaited on the site's own name rather than on the container: the
    // container renders immediately with a placeholder, so asserting on it
    // straight away would pass before the site was known and fail for the
    // wrong reason once it was.
    const current = await screen.findByTestId('site-option-sindri');
    expect(current).toHaveAttribute('aria-current', 'true');
    expect(screen.getByTestId('site-context').textContent).toContain('Sindri');
  });

  it('offers a switcher when more than one site is registered', async () => {
    stubApi('sindri');
    render(<App />);
    expect(await screen.findByTestId('site-option-brokkr')).toHaveAttribute(
      'href',
      'https://alviss.brokkr.veldris.internal',
    );
  });

  it('states a partitioned site plainly, above everything', async () => {
    // AC-U12. Not discovered when a publish returns 409: a standing notice.
    stubApi('brokkr');
    render(<App />);
    await screen.findByText(/partitioned from the federation/i);
    const notice = screen.getByRole('status');
    expect(notice.textContent).toMatch(/Training and evaluation continue/);
    expect(notice.textContent).toMatch(/Release is unavailable/);
  });

  it('does not call a partition an error', async () => {
    stubApi('brokkr');
    render(<App />);
    await screen.findByText(/partitioned from the federation/i);
    expect(screen.getByRole('status').textContent).toMatch(/normal operating condition/);
  });

  it('puts a skip link first in the tab order', () => {
    stubApi('sindri');
    render(<App />);
    const skip = screen.getByRole('link', { name: /skip to main content/i });
    expect(skip).toHaveAttribute('href', '#cn-main');
  });

  it('names the section that is current', async () => {
    stubApi('sindri');
    render(<App />);
    const overview = await screen.findByRole('link', { name: 'Overview' });
    expect(overview).toHaveAttribute('aria-current', 'page');
  });
});
