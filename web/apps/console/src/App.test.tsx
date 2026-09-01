import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { App } from './App';

describe('App', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ status: 'ok', version: '0.1.0', site_id: 'sindri' }),
      }),
    );
  });

  it('names the system', async () => {
    render(<App />);
    expect(screen.getByRole('heading', { name: 'DRAUPNIR' })).toBeInTheDocument();
    // The health probe resolves after the first paint; awaiting it keeps the
    // assertion above honest and keeps React from warning about an update
    // outside act().
    await screen.findByText(/site sindri/);
  });

  it('reports the API status once it answers', async () => {
    render(<App />);
    expect(await screen.findByText('ok, site sindri')).toBeInTheDocument();
  });
});
